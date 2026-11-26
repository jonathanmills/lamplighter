# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Jonathan Mills
"""LDAP directory: user identity and group membership.

TLS only: ldaps://, or ldap:// with StartTLS, always with certificate
verification (unless ``allow_insecure`` is set for tests). Supports
RFC 2307bis (groupOfNames + memberOf), RFC 2307 (posixGroup +
memberUid) and Active Directory.

python-ldap is synchronous; call these methods from a worker thread
(FastAPI does this for plain ``def`` endpoints and dependencies).
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field

import ldap
import ldap.dn
import ldap.filter
from ldap.controls import SimplePagedResultsControl

from .config import DirectoryType, LDAPConfig, read_secret

log = logging.getLogger(__name__)

_SCOPES = {"sub": ldap.SCOPE_SUBTREE, "one": ldap.SCOPE_ONELEVEL}


def norm_dn(dn: str) -> str:
    """Normalize a DN for comparison (case, spacing, escaping)."""
    try:
        return ldap.dn.dn2str(ldap.dn.str2dn(dn)).lower()
    except ldap.DECODING_ERROR:
        return dn.strip().lower()


@dataclass(frozen=True)
class DirUser:
    uid: str
    dn: str
    name: str
    mail: str | None
    uid_number: int | None
    gid_number: int | None
    groups: tuple[str, ...] = field(default_factory=tuple)  # normalized DNs


class DirectoryError(RuntimeError):
    """The directory could not be reached or misbehaved."""


def _first(entry: dict, attr: str) -> str | None:
    values = entry.get(attr) or entry.get(attr.lower())
    if not values:
        # python-ldap preserves the server's attribute case; match loosely.
        for k, v in entry.items():
            if k.lower() == attr.lower():
                values = v
                break
    return values[0].decode("utf-8") if values else None


def _all(entry: dict, attr: str) -> list[str]:
    for k, v in entry.items():
        if k.lower() == attr.lower():
            return [x.decode("utf-8") for x in v]
    return []


@dataclass(frozen=True)
class Person:
    uid: str
    name: str
    mail: str | None
    uid_number: int | None


@dataclass(frozen=True)
class UnixGroup:
    cn: str
    description: str | None
    gid_number: int | None
    members: int


class Directory:
    def __init__(self, cfg: LDAPConfig):
        self.cfg = cfg
        self._cache: dict[str, tuple[float, DirUser]] = {}
        self._lock = threading.Lock()
        self._bulk: dict[str, tuple[float, dict]] = {}

    # --- connections -------------------------------------------------

    def _connect(self):
        last_error: Exception | None = None
        for url in self.cfg.servers:
            try:
                conn = ldap.initialize(url)
                conn.set_option(ldap.OPT_PROTOCOL_VERSION, 3)
                conn.set_option(ldap.OPT_REFERRALS, 0)
                conn.set_option(ldap.OPT_NETWORK_TIMEOUT, self.cfg.timeout_seconds)
                conn.set_option(ldap.OPT_TIMEOUT, self.cfg.timeout_seconds)
                if self.cfg.ca_file:
                    conn.set_option(ldap.OPT_X_TLS_CACERTFILE, self.cfg.ca_file)
                conn.set_option(ldap.OPT_X_TLS_REQUIRE_CERT,
                                ldap.OPT_X_TLS_NEVER if self.cfg.allow_insecure
                                else ldap.OPT_X_TLS_DEMAND)
                conn.set_option(ldap.OPT_X_TLS_NEWCTX, 0)  # must come last
                if url.startswith("ldap://") and self.cfg.starttls:
                    conn.start_tls_s()
                return conn
            except ldap.LDAPError as exc:
                log.warning("ldap: cannot connect to %s: %s", url, exc)
                last_error = exc
        raise DirectoryError(f"no LDAP server reachable: {last_error}")

    def _service_conn(self):
        conn = self._connect()
        try:
            conn.simple_bind_s(self.cfg.bind_dn, read_secret(self.cfg.bind_password_file))
        except ldap.LDAPError as exc:
            conn.unbind_s()
            raise DirectoryError(f"service bind failed: {exc}") from exc
        return conn

    # --- lookups -----------------------------------------------------

    def _search_user(self, conn, username: str) -> tuple[str, dict] | None:
        u = self.cfg.users
        flt = u.filter.format(username=ldap.filter.escape_filter_chars(username))
        attrs = [u.login_attr, u.name_attr, u.mail_attr, u.uidnumber_attr,
                 u.gidnumber_attr, "memberOf"]
        res = [r for r in conn.search_s(u.base_dn, _SCOPES[u.scope], flt, attrs) if r[0]]
        if len(res) != 1:
            if len(res) > 1:
                log.warning("ldap: %d entries match user %r; refusing", len(res), username)
            return None
        return res[0]

    def _groups_of(self, conn, user_dn: str, entry: dict, uid: str) -> tuple[str, ...]:
        g = self.cfg.groups
        if g.membership == "memberof":
            direct = _all(entry, "memberOf")
        else:
            if self.cfg.directory_type is DirectoryType.rfc2307:
                member_flt = f"(memberUid={ldap.filter.escape_filter_chars(uid)})"
            else:
                member_flt = f"(member={ldap.filter.escape_filter_chars(user_dn)})"
            res = conn.search_s(g.base_dn, _SCOPES[g.scope],
                                f"(&{g.filter}{member_flt})", ["1.1"])
            direct = [dn for dn, _ in res if dn]

        seen = {norm_dn(d): d for d in direct}
        if g.nested and self.cfg.directory_type is not DirectoryType.rfc2307:
            frontier = list(seen.values())
            for _depth in range(10):  # bounded: guards against cycles
                nxt = []
                for gdn in frontier:
                    flt = f"(&{g.filter}(member={ldap.filter.escape_filter_chars(gdn)}))"
                    for dn, _ in conn.search_s(g.base_dn, _SCOPES[g.scope], flt, ["1.1"]):
                        if dn and norm_dn(dn) not in seen:
                            seen[norm_dn(dn)] = dn
                            nxt.append(dn)
                if not nxt:
                    break
                frontier = nxt
        return tuple(sorted(seen))

    def _build_user(self, conn, dn: str, entry: dict) -> DirUser:
        u = self.cfg.users
        uid = _first(entry, u.login_attr) or ""
        num = lambda a: int(v) if (v := _first(entry, a)) and v.isdigit() else None
        return DirUser(uid=uid, dn=dn, name=_first(entry, u.name_attr) or uid,
                       mail=_first(entry, u.mail_attr),
                       uid_number=num(u.uidnumber_attr), gid_number=num(u.gidnumber_attr),
                       groups=self._groups_of(conn, dn, entry, uid))

    def find_user(self, username: str, *, refresh: bool = False) -> DirUser | None:
        """Look a user up by login name (cached for cache_ttl_seconds)."""
        key = username.lower()
        now = time.monotonic()
        if not refresh:
            with self._lock:
                hit = self._cache.get(key)
            if hit and hit[0] > now:
                return hit[1]
        conn = self._service_conn()
        try:
            found = self._search_user(conn, username)
            user = self._build_user(conn, *found) if found else None
        except ldap.LDAPError as exc:
            raise DirectoryError(f"lookup of {username!r} failed: {exc}") from exc
        finally:
            conn.unbind_s()
        with self._lock:
            if user:
                self._cache[key] = (now + self.cfg.cache_ttl_seconds, user)
            else:
                self._cache.pop(key, None)
        return user

    def authenticate(self, username: str, password: str) -> DirUser | None:
        """Verify a password by binding as the user. None if it fails."""
        # An empty password would make a "successful" unauthenticated
        # bind (RFC 4513 section 5.1.2), so refuse it outright.
        if not username or not password:
            return None
        user = self.find_user(username, refresh=True)
        if user is None:
            return None
        conn = self._connect()
        try:
            conn.simple_bind_s(user.dn, password)
            return user
        except ldap.INVALID_CREDENTIALS:
            return None
        except ldap.LDAPError as exc:
            raise DirectoryError(f"bind as {username!r} failed: {exc}") from exc
        finally:
            conn.unbind_s()

    # --- bulk listings (one paged search each, cached) ----------------

    @staticmethod
    def _paged(conn, base: str, scope: int, flt: str, attrs: list[str], page: int = 500):
        """Yield (dn, entry) over all pages, so large directories never hit
        the server's size limit."""
        ctrl = SimplePagedResultsControl(True, size=page, cookie=b"")
        while True:
            msgid = conn.search_ext(base, scope, flt, attrs, serverctrls=[ctrl])
            _, data, _, ctrls = conn.result3(msgid)
            for dn, entry in data:
                if dn:
                    yield dn, entry
            cookie = next((c.cookie for c in ctrls
                           if c.controlType == SimplePagedResultsControl.controlType), b"")
            if not cookie:
                return
            ctrl.cookie = cookie

    def _cached_bulk(self, key: str, build) -> dict:
        now = time.monotonic()
        with self._lock:
            hit = self._bulk.get(key)
        if hit and hit[0] > now:
            return hit[1]
        conn = self._service_conn()
        try:
            data = build(conn)
        except ldap.LDAPError as exc:
            raise DirectoryError(f"listing {key} failed: {exc}") from exc
        finally:
            conn.unbind_s()
        with self._lock:
            self._bulk[key] = (now + self.cfg.cache_ttl_seconds, data)
        return data

    def all_people(self) -> dict[str, Person]:
        """Every person in the users subtree, by login name."""
        u = self.cfg.users

        def build(conn) -> dict[str, Person]:
            flt = u.filter.replace("{username}", "*")
            out: dict[str, Person] = {}
            for _, e in self._paged(conn, u.base_dn, _SCOPES[u.scope], flt,
                                    [u.login_attr, u.name_attr, u.mail_attr, u.uidnumber_attr]):
                uid = _first(e, u.login_attr)
                if uid:
                    num = _first(e, u.uidnumber_attr)
                    out[uid] = Person(uid=uid, name=_first(e, u.name_attr) or uid,
                                      mail=_first(e, u.mail_attr),
                                      uid_number=int(num) if num and num.isdigit() else None)
            return out
        return self._cached_bulk("people", build)

    def all_unix_groups(self) -> dict[str, UnixGroup]:
        """Groups that carry a gidNumber (Unix groups), by name."""
        g = self.cfg.groups

        def build(conn) -> dict[str, UnixGroup]:
            out: dict[str, UnixGroup] = {}
            for _, e in self._paged(conn, g.base_dn, _SCOPES[g.scope], f"(&{g.filter}(gidNumber=*))",
                                    ["cn", "description", "gidNumber", "member", "memberUid"]):
                cn = _first(e, "cn")
                if cn:
                    num = _first(e, "gidNumber")
                    out[cn] = UnixGroup(cn=cn, description=_first(e, "description"),
                                        gid_number=int(num) if num and num.isdigit() else None,
                                        members=len(_all(e, "member")) or len(_all(e, "memberUid")))
            return out
        return self._cached_bulk("unix_groups", build)

    def test_connection(self, sample_user: str | None = None) -> dict:
        """Diagnostics for the admin "test connection" page."""
        result: dict = {"servers": self.cfg.servers, "bind_dn": self.cfg.bind_dn}
        conn = self._service_conn()
        try:
            result["bind"] = "ok"
            result["whoami"] = conn.whoami_s()
        finally:
            conn.unbind_s()
        if sample_user:
            user = self.find_user(sample_user, refresh=True)
            result["sample_user"] = None if user is None else {
                "dn": user.dn, "uid": user.uid, "groups": list(user.groups)}
        return result
