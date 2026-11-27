# Naming this fork: Lamplighter or Lustrum?

*Status: open discussion, as of November 2026. No rename has happened yet: the code, packages and commands still use the Robinhood names.*

This fork of the [Robinhood Policy Engine](https://github.com/cea-hpc/robinhood) will probably get a new name. This document sets out why, the two finalists, and the reasoning on each side. Comments are welcome. Please open an issue.

## Why rename at all

Robinhood is an excellent tool with an unfortunate name. Today "Robinhood" means a stock-trading app first and an English folk hero second. Someone who hears the name will almost never think of a policy engine for the Lustre filesystem.

CEA, which created Robinhood, has never published where the name came from. The likeliest explanation is the tool's origin as a purger for shared scratch filesystems. It takes from the rich (the users and storage targets holding too much) to give to the poor (free space for everyone).

The new name should carry these ideas:

- **Light.** Lustre means shine. The Latin *lux*, stars and bright objects in the sky are all in play.
- **Illumination.** The tool's real value is that it *illuminates* a filesystem: who owns what, how old it is, where the space is going, and what can safely go.

## The finalists at a glance

| | **Lamplighter** | **Lustrum** |
|---|---|---|
| What it is | The person who walked the streets at dusk lighting the lamps, and put them out again at dawn | The Roman purification of the whole people, performed by a censor once the census was complete, about every five years |
| Who understands it at once | Practically every English speaker | Readers of Latin and students of Roman history. In the Netherlands, a *lustrum* is also a student society's five-yearly anniversary |
| Connection to Lustre | Thematic: light | Linguistic: "lustre" descends from Latin *lūstrāre*, which is related to *lūstrum* |
| How much story | One image, instantly clear | A layered story that rewards anyone who looks it up |
| Biggest risk | A common word, with some pop-culture baggage | Obscure, and easy to mistake for a misspelling of "Lustre" |

## The case for Lamplighter

**The image is the explanation.** Nobody needs to be told what a lamplighter does, and his work maps neatly onto the tool:

| The lamplighter… | The software… |
|---|---|
| walks the same rounds every night | scans the filesystem and follows Lustre's MDT changelog |
| lights the lamps so people can see the street | produces reports, `find`/`du` tools and a web UI, so admins can see the filesystem |
| puts out the lamps at dawn | runs purge policies to clear what's no longer needed |
| tends the city's lights | looks after shared storage |

**It's warm and approachable.** It's a friendly name for a tool whose job includes deleting other people's files. It's easy to say, easy to spell, and easy to draw: a lamp, a flame, a lamp-post.

**It has a French connection.** The lamplighter (*l'allumeur de réverbères*) is one of the best-loved characters in Saint-Exupéry's *Le Petit Prince*, a fitting nod to Robinhood's French origins at CEA.

**Weaknesses:**
- **A common word.** Web searches for it are crowded with literature, history, and businesses of every kind, so it needs "Lustre" or "HPC" next to it to be found.
- **Pop-culture baggage.** Lamplighter is the name of a supporting character, a fire-wielding superhero with a dark backstory, in the Amazon series *The Boys*.
- **The link to Lustre is only thematic.** The connection is the idea of light, not the word.
- **It's long:** 11 letters. That's fine for a project or package name, but too long for a command prefix.

## The case for Lustrum

**It's an ancestor of the word "Lustre".** English *lustre* comes through French *lustre* and Italian *lustro* from Latin *lūstrāre*, a verb tied to *lūstrum*. (How *lūstrum* itself formed is disputed: some connect it with *luō*/*lavō*, "to wash", and others with the root of *lux*. Either way, the line from *lūstrum* to "Lustre" holds.)

**The ritual matches the software.** The lustrum was the purification of the whole Roman people, carried out by one of the censors on the Campus Martius after the census was taken, with the sacrifice of a pig, a sheep and a bull (the *suovetaurilia*). Livy (1.44) describes the first one. After Servius Tullius finished his census, he ordered the citizens to assemble under arms on the Campus Martius at first light and purified the whole army there. This was called *lustrum conditum* because it marked the end of the census-taking. First you count everything, then you purify. That's how this tool works: it scans the whole filesystem into a database (the census), then runs policies, purges among them, on what it found.

**The verb covers almost the whole tool.** *Lūstrō* has an unusually wide range of meanings, and nearly every one matches a feature:

| Sense of *lūstrō* | Feature |
|---|---|
| to purify by sacrifice | purge policies |
| to circle, go around (the officiant walked around those being purified) | the scan; following the changelog |
| to traverse, go all through (cf. *perlūstrō*) | full filesystem scans |
| to review (troops), examine | reports and statistics |
| to survey, observe, search | `find`, `du` and classification rules |
| of the sun, to illuminate (*Aeneid* 4.607: *Sol, qui terrarum flammis opera omnia lustras*) | the web UI: making the filesystem visible |

**The name is nearly unused.** In November 2026 it was free on PyPI, and the few GitHub projects using it were small and unrelated to storage or HPC.

**Weaknesses:**
- **Few people know the word.** To most English speakers it's either unknown or a rare literary word for "a period of five years". It needs explaining, and most people never read the explanation.
- **It looks like a typo.** Next to "Lustre", "Lustrum" can read as a misspelling, and search engines may "correct" it.
- **The "five years" meaning.** It's the first definition most dictionaries give, and it suggests something slow and periodic, the opposite of a tool that follows changelogs in near real time.
- **The homograph.** Short-u *lŭstrum* is a wild beasts' den, and figuratively a den of vice. Latinists will notice; almost no one else will.

## Head-to-head

| Criterion | Lamplighter | Lustrum | Edge |
|---|---|---|---|
| Instantly understood | Yes | No; needs explaining | Lamplighter |
| Depth of meaning | One good image | Etymology, ritual, and a verb that covers the whole tool | Lustrum |
| Fit to what the tool does | Strong | Stronger | Lustrum |
| Connection to Lustre | Thematic | Linguistic | Lustrum |
| Findability in search | Crowded, but no confusion | Clean, but can be taken for a typo of "Lustre" | Even |
| Pop-culture baggage | *The Boys* | A Robert Harris novel | Lustrum |
| Easy to say and spell | Yes | Mostly | Lamplighter |
| International audience | Plain English; *Le Petit Prince* in French | Roots familiar in Romance languages; an everyday word in Dutch | Even |
| Tone | Warm, friendly | Scholarly, dignified | A matter of taste |
| Logo / visual identity | Obvious | Possible (laurel, Roman lettering) | Lamplighter |

**In short:** Lustrum wins on *meaning*, and Lamplighter wins on *reach*. Lustrum is the better name for people who hear the explanation. Lamplighter is the better name for people who don't, and in open source that's most people. Most will meet the name once, in a search result, a mailing-list subject line or a slide title.

## Ways to have both

1. **Lamplighter as the name, Lustrum as the story.** The README would tell both stories: the lamplighter on his rounds, and the Roman census and lustrum the tool is modeled on. Possible tagline: *"Lamplighter: a lustrum for your Lustre filesystem."*
2. **Use *lustrum* as a term inside the tool.** For example, a full scan-report-purge cycle could be called a "lustrum", or the web UI's census page could be named "Lustrum".
3. **Roman release codenames**, named after Servius Tullius or notable censors.

## Questions that will decide it

- **Who needs to find it?** If the goal is adoption by strangers at other sites, the name has to work with no context, which favors Lamplighter. If the audience is small and will hear the story, Lustrum's depth costs little.
- **Where will people first meet it?** A conference talk can tell the Lustrum story. A package search result cannot.
- **The mailing-list test:** compare "*[ANN] Lustrum 1.0: a policy engine for Lustre*" with "*[ANN] Lamplighter 1.0: a policy engine for Lustre*".

## Current leaning

**Lamplighter as the name, with Lustrum as its story**, though the question is still open.

## Settled either way

- **The command prefix becomes `lux-`** (Latin for light), replacing `rbh-`: `lux-find`, `lux-du`, `lux-report` and so on.
  - Rejected: `lamp-` (reads like the LAMP stack) and `ll-` (used by Lustre's own tools, such as `llapi` and `llstat`).
- **Other names considered and dropped:** Canopus (crowded, and it clashes with an established scientific tool of the same name). Also avoided as overused: Lumen, Illumina, Polaris, Sirius, Vega, Gaia, Lucent.
- **Attribution stays.** Whatever the name, this remains a derivative of the Robinhood Policy Engine by CEA, distributed under the CeCILL-C license, and CEA's copyright notices stay in place.
- **The rename will be gradual.** Names users see (project, packages, commands, documentation) would change first, with compatibility links for the old `rbh-*` commands. Internal source identifiers would stay as they are, to keep merging upstream changes easy.
