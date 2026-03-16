# World In a Pie for Fictional Universe Management

## The Idea

World In a Pie is a generic, domain-agnostic storage and reporting engine. Most of its documented use cases lean toward the serious — clinical trial data, B2B catalog reconciliation, legal document management. But the architecture makes no distinction between a regulatory submission and a hobbit. The primitives — terminologies, templates, versioned documents, typed ontology relationships, Registry synonyms — are exactly what a complex fictional universe demands.

This document explores using WIP to track characters, relationships, bloodlines, factions, places, and events across fictional works. It works equally well for published universes like Tolkien's Middle-earth or George R.R. Martin's Westeros, and for original fiction being actively written. The MCP server integration with Claude closes the loop — turning the structured data into a queryable, conversational continuity assistant.

---

## Why Fictional Universes Are Hard to Track

The naive approach — a wiki, a spreadsheet, a folder of notes — breaks down quickly for any universe of meaningful complexity. The problems are familiar:

- **Identity ambiguity**: the same character has many names, titles, and aliases across different contexts
- **Disputed facts**: two sources disagree about a character's parentage, the date of an event, or the outcome of a battle
- **Cross-work continuity**: a character appears in works by different authors, or the same author returns to a character decades later
- **Version history**: a character changes — is resurrected, renamed, transformed — and earlier references need to remain valid
- **Relationship complexity**: bloodlines, alliances, enmities, and mentorships form a dense graph that changes over time
- **Scale**: a universe like LOTR or GoT has hundreds of named characters, thousands of relationships, and decades of in-universe timeline

WIP solves all of these as a natural consequence of its core architecture. None of it requires special-casing for fiction.

---

## The Identity Problem in Fictional Universes

### Tolkien Will Break a Naive System Immediately

The Registry's synonym mechanism earns its existence the moment you try to model Gandalf.

Gandalf is known by at least six names across different cultures, languages, and periods of his existence:

| Name | Used by | Context |
|------|---------|---------|
| Gandalf | Men of the North | His most common name in The Hobbit and LOTR |
| Mithrandir | Elves (and Gandalf himself) | Sindarin, meaning "Grey Pilgrim" |
| Olórin | Valinor | His original Maia name before coming to Middle-earth |
| Greyhame | Rohirrim | "Grey-cloak" in the language of Rohan |
| Incánus | Haradrim | His name in the south |
| Tharkûn | Dwarves | His Khuzdul name |
| The Grey Pilgrim | Various | Descriptive title |
| The White Rider | Various | After his return as Gandalf the White |

Without synonym management, you end up with eight Gandalfs. Queries for "all scenes featuring Gandalf" miss every scene where he is called Mithrandir. Cross-references from Elvish texts to Common Tongue texts break. The bibliography of a serious Tolkien scholar becomes incomprehensible.

In WIP, there is one canonical CHARACTER document for Gandalf. Every name above is a registered synonym. Any reference to any of these names resolves to the same entity. A query for "all relationships involving this character" returns the complete picture regardless of which name the source used.

### Aragorn — Synonyms with Temporal Metadata

Aragorn's names are not just aliases — they belong to specific periods of his life and are used by specific groups. This is richer than a flat synonym list:

| Name | Period | Used by |
|------|--------|---------|
| Estel | Childhood in Rivendell | Elves (meaning "Hope", his true identity hidden) |
| Strider | Third Age, wandering years | Men of Bree and the Shire |
| Dúnedain Chieftain | Throughout | Rangers of the North |
| Thorongil | Service under Steward Ecthelion | Men of Gondor |
| Aragorn son of Arathorn | His true name | Used formally |
| Elessar | His kingship name | Elvish, meaning "Elfstone" |
| King Elessar Telcontar | Coronation and beyond | Official royal title |

The synonym mechanism handles the identity reconciliation. The temporal and contextual metadata about *when* and *by whom* each name is used becomes fields on the synonym record itself, making it queryable: "what was Aragorn known as during his service in Gondor?"

### Jon Snow — Disputed Identity as a First-Class Concept

Jon Snow's true parentage — the son of Rhaegar Targaryen and Lyanna Stark, making him Aegon Targaryen, heir to the Iron Throne — is one of the most consequential identity revelations in Game of Thrones. But for most of the story, it is disputed, unknown, or deliberately concealed.

A naive system either stores the "correct" answer and loses the history of uncertainty, or stores both and has no way to express confidence or provenance. WIP handles this naturally:

```
PARENTAGE_CLAIM document:
  subject: Jon Snow (CHARACTER document)
  claimed_parent: Eddard Stark (CHARACTER document)
  claim_type: "paternal"
  source: "Common knowledge in Westeros"
  confidence: "believed"
  valid_until: Season 7

PARENTAGE_CLAIM document:
  subject: Jon Snow (CHARACTER document)  
  claimed_parent: Rhaegar Targaryen (CHARACTER document)
  claim_type: "paternal"
  source: "Tower of Joy vision, Samwell Tarly records"
  confidence: "confirmed"
  valid_from: Season 7
```

Both claims coexist as versioned documents. Queries can filter by confidence, by source, or by point-in-timeline. The history of what was believed and when is preserved. Claude, querying via MCP, can answer "what was believed about Jon's parentage before Season 7?" and "what was confirmed afterward?" as distinct questions.

---

## The Ontology Layer — Typed Relationships

Simple document references are not enough for a fictional universe. The *type* of relationship carries meaning that changes what queries are possible and what conclusions are valid.

### Core Relationship Types

```
# Blood and family
CHARACTER --[parent_of]--> CHARACTER
CHARACTER --[sibling_of]--> CHARACTER
CHARACTER --[married_to]--> CHARACTER
CHARACTER --[adopted_by]--> CHARACTER
CHARACTER --[descended_from]--> CHARACTER  (supports polyhierarchy traversal)

# Transformation and continuity
CHARACTER --[reincarnation_of]--> CHARACTER
CHARACTER --[successor_to]--> CHARACTER
CHARACTER --[same_entity_as]--> CHARACTER   (cross-author appearances)
CHARACTER --[alter_ego_of]--> CHARACTER

# Social and political
CHARACTER --[mentor_of]--> CHARACTER
CHARACTER --[sworn_to]--> CHARACTER
CHARACTER --[betrayed]--> CHARACTER
CHARACTER --[allied_with]--> CHARACTER
CHARACTER --[enemy_of]--> CHARACTER
CHARACTER --[member_of]--> FACTION
CHARACTER --[rules]--> PLACE

# Events
CHARACTER --[killed_by]--> CHARACTER
CHARACTER --[killed]--> CHARACTER
CHARACTER --[present_at]--> EVENT
EVENT --[caused_by]--> CHARACTER
EVENT --[resulted_in]--> EVENT
EVENT --[preceded_by]--> EVENT

# Narrative
CHARACTER --[appears_in]--> WORK
WORK --[set_in]--> PLACE
WORK --[canonical_in]--> UNIVERSE
WORK --[contradicts]--> WORK
WORK --[expands_on]--> WORK

# Legal and inheritance
CHARACTER --[heir_to]--> CHARACTER
CHARACTER --[claims_title_of]--> CHARACTER
FACTION --[vassal_of]--> FACTION
FACTION --[enemy_of]--> FACTION
```

### Why Typed Relationships Matter

Consider these two statements about the same pair of characters:

- Boromir *is the brother of* Faramir
- Boromir *died before* Faramir became Steward

The first is a `sibling_of` relationship. The second is an `EVENT --[preceded_by]--> EVENT` chain with character involvement. They are fundamentally different kinds of assertions requiring different query patterns. A system that stores both as generic "links" cannot distinguish them. WIP's typed ontology relationships can.

The traversal query capability means you can ask: "give me all characters descended from Númenórean kings" — and the system walks the `descended_from` graph across multiple generations, returning Aragorn, Boromir, Faramir, Denethor, and the full lineage, without you having to know how many generations deep it goes.

### The Gandalf the Grey / Gandalf the White Problem

Is Gandalf the Grey the same entity as Gandalf the White?

Theologically in Tolkien's universe: he was sent back by the Valar, his mission unfulfilled, in an enhanced form. Narratively: same memories, same essential personality, but transformed. Technically in WIP terms: **a new document version of the same identity**.

```
CHARACTER document version 1:
  canonical_name: Gandalf
  order: Istari
  colour: Grey
  status: active (until Fellowship of the Ring, Moria)

CHARACTER document version 2:
  canonical_name: Gandalf  
  order: Istari
  colour: White
  status: active (from The Two Towers onward)
  supersedes: version 1
```

Same identity hash — because the identity fields (name, order) are unchanged. New version — because the attributes changed. Version 1 is deactivated when version 2 is created, but remains queryable. References from text written before his return correctly resolve to version 1. References from text written after resolve to version 2 by default, or to the specific version if pinned.

This is WIP's versioning behaviour applied without modification to a fictional metaphysics problem.

---

## Cross-Work Identity — The Hard Case

### LOTR and The Hobbit

Tolkien's own works span decades of publication and in-universe centuries of timeline. Bilbo Baggins appears in The Hobbit as a middle-aged hobbit on an adventure, and in LOTR as a very old hobbit at his eleventy-first birthday party, and again briefly in the appendices.

These are not different characters. They are the same entity at different points in time, appearing in different works. WIP handles this with a single canonical CHARACTER document for Bilbo, with work appearances tracked as relationships:

```
CHARACTER --[appears_in {chapters: [...], timeline_period: "TA 2941"}]--> THE_HOBBIT
CHARACTER --[appears_in {chapters: [...], timeline_period: "TA 3001-3021"}]--> LOTR
```

Queries for "all of Bilbo's appearances across Tolkien's works" return the complete picture. Queries for "who appears in both The Hobbit and LOTR" are trivial joins on the reporting layer.

### Cross-Author Appearances

The richer problem is characters who appear in works by different authors — authorised sequels, expanded universe novels, fan fiction treated as canonical within a particular reading community, or deliberate crossovers.

Frodo appearing in a derivative work by another author, or Sherlock Holmes appearing in one of the hundreds of continuation novels by different writers, requires establishing a relationship between the canonical entity (defined in the original work's namespace) and the derivative appearance.

In WIP:

```
WORK namespace: tolkien-canonical
  CHARACTER: Frodo Baggins (canonical entity)

WORK namespace: derivative-author-x
  CHARACTER: Frodo Baggins → synonym registered pointing to tolkien-canonical entity
```

The derivative work's character *is* the canonical character, via synonym. Queries across namespaces ("all works featuring Frodo, across all authors") work out of the box. The canonical namespace remains authoritative. Derivative namespaces extend it without polluting it.

This also handles the Sherlock Holmes universe, where the canonical Conan Doyle character has been extended, reinterpreted, and appeared in cross-universe stories by hundreds of authors. One canonical entity. Hundreds of synonyms and appearances. All queryable.

---

## The GoT Blood Relationship Graph

Game of Thrones is a torture test for any relationship tracking system. The Targaryen family tree alone features:

- Multi-generational incestuous marriages (uncle-niece, cousin-cousin, sibling-sibling in historical cases)
- Disputed parentage (most famously Jon Snow, but also questions around several others)
- Characters who don't know their own identity
- Retroactive reveals that recontextualise earlier relationships
- Parallel claims to the same title by multiple characters simultaneously

The polyhierarchy traversal in WIP's ontology means you can ask: "list all living characters with a valid claim to the Iron Throne, ranked by primogeniture" — and the system walks the `descended_from` and `heir_to` graph, applies the `killed` relationships to exclude dead claimants, and returns the result.

The disputed parentage pattern (described above for Jon Snow) applies to any contested claim. Each claim is a document with a source, a confidence level, and a validity period. The database does not need to pick a winner — it stores all claims and lets the query decide which to surface.

### The Frey Problem — Scale

House Frey has, canonically, over one hundred living members at the time of the Red Wedding. Tracking all of them, their relationships to Walder Frey, their sub-family branches, and their fates after the Red Wedding is the kind of scale problem that breaks a spreadsheet. WIP's bulk import APIs, combined with a well-designed template for `NOBLE_HOUSE_MEMBER` with identity fields `[house, given_name, generation]`, handles this as a data loading exercise. The ontology traversal then answers questions like "how many of Walder Frey's direct grandchildren survived the Red Wedding?" without manual counting.

---

## For Original Fiction — The Writer's Assistant

The use case that may be most practically valuable is not tracking existing published universes but supporting an original work in progress.

### The Continuity Problem

Three hundred pages into a novel, a writer faces questions like:

- Have these two characters ever been in the same location at the same time?
- What colour did I say her eyes were in chapter two?
- Did I establish whether this character knows about the treaty?
- Is the timeline consistent — could this character have travelled from the northern city to the coast in the time the narrative allows?
- Does this backstory reveal contradict anything established earlier?

These questions currently get answered by Ctrl+F in a manuscript, by index cards pinned to a corkboard, by a wiki that drifted out of sync with the actual text three rewrites ago, or by not answering them at all and hoping the copy editor catches it.

### WIP as World-Building Infrastructure

The writer maintains their world-building as WIP documents *as they write*, not as a separate documentation exercise:

- Each new character introduced becomes a CHARACTER document
- Each scene becomes an EVENT document with characters present, location, and timeline position
- Each relationship established becomes an ontology entry
- Each piece of backstory revealed becomes a LORE document with the chapter it appears in as provenance

The templates enforce consistency — you cannot create a CHARACTER without specifying identity fields, so you never end up with two characters who are subtly the same person under different names.

### Querying Your Own Manuscript via Claude

With the MCP server connecting Claude to the WIP instance containing your world, the continuity queries become conversational:

> *"Have Alara and the merchant from chapter one ever been in the same place?"*

Claude traverses EVENT documents, filters by character presence, and answers from your actual manuscript data — not from hallucination, not from a faulty memory of what you wrote, but from the structured record you've been maintaining.

> *"What does Alara know about the northern conspiracy at this point in the story?"*

Claude queries LORE documents filtered by the character's presence at scenes where relevant information was revealed, and returns a structured account of what she could know, when she learned it, and from whom.

> *"Is the journey from the capital to the coastal town feasible in four days given what I've established about travel times?"*

Claude queries PLACE documents for distance and terrain, EVENT documents for established travel precedents, and reasons about consistency.

> *"Does anything I'm about to reveal in chapter fifteen contradict what I established in chapters one through five?"*

This is the most powerful query. Claude reads the proposed reveal, queries the relevant documents in WIP, and surfaces any contradictions — before they go to an editor or a reader.

### Cross-Canon Consistency

For writers working in established universes — fan fiction, authorised tie-ins, shared world anthologies — the canonical universe loads into a separate WIP namespace. The writer's original content lives in their own namespace.

> *"Does anything in my story contradict established LOTR canon?"*

Claude reasons across both namespaces simultaneously, flagging any assertion in the writer's documents that conflicts with the canonical namespace. The canonical data is read-only and authoritative. The writer's namespace extends it.

---

## The Fan Experience — This Is Where It Gets Dangerous

Everything described so far frames the MCP + Claude integration as a query interface. That undersells it dramatically.

What WIP + Claude actually is, for a fan, is **the one person in the world who has read everything, remembers everything, has an opinion about everything, and is available at 2am when the argument gets heated.**

This is not a search engine. It is a conversation partner who happens to have perfect recall of an entire fictional universe, grounded in structured data that prevents it from making things up. Real fans would not use this occasionally. Real fans would lose entire weekends to it.

---

### A GoT Fan Rewatching Their Favourite Episode

It starts innocuously.

> **Fan:** I'm rewatching the Red Wedding. Remind me — how many Starks were actually at the Twins that night?

Claude queries EVENT documents for the Red Wedding, filters CHARACTER presence, and responds with a precise list — Robb, Catelyn, and a named party of northern lords — with references to which episode and chapter established each presence.

> **Fan:** And how many of the Frey bannermen who were there had previously sworn oaths to House Stark?

Now Claude traverses the `sworn_to` and `vassal_of` ontology relationships, cross-referencing characters present at the event against historical allegiance records. It surfaces three names, with the episodes where their oaths were established.

> **Fan:** So Walder Frey knew exactly which of his own men had betrayed their oaths when he planned this. That's what makes it so cold.

> **Claude:** It goes further than that. Two of those men were present at the oath-taking itself — they were in the same room when Robb Stark swore to marry a Frey daughter. The WIP records show their presence at both events. Walder Frey didn't just use oath-breakers. He used witnesses.

This is the moment. Claude didn't just answer a query — it synthesised across two EVENT documents, found a connection the fan hadn't asked about, and handed them a new way to think about a scene they've watched a dozen times.

The fan is not closing this conversation any time soon.

> **Fan:** What would have happened to the Frey line if Robb had actually married one of Walder's daughters as promised?

Now Claude is reasoning about counterfactuals, using the `heir_to` and `claims_title_of` graph to trace what alliances would have been cemented, which rivalries would have been defused, which characters' story arcs would never have existed.

This is a conversation. It has no natural end point. Real fans know this feeling.

---

### Two LOTR Fans Arguing About Dwarf Genealogy

This is the scenario that makes Tolkien fandom what it is. The argument starts somewhere reasonable and ends somewhere no sane person expected to be.

> **Fan A:** Durin's Folk are the most important dwarf clan. Thorin's company alone changed the entire history of Middle-earth.

> **Fan B:** But how many of Thorin's company were actually direct descendants of Durin the Deathless? I think you're overstating the lineage.

Neither of them actually knows the answer with certainty. They pull up WIP.

> **Fan A:** How many members of Thorin's company can trace direct lineage to Durin the Deathless?

Claude traverses the `descended_from` graph from each of the thirteen dwarves, walking multiple generations through the WIP CHARACTER documents, cross-referencing the genealogy records populated from the appendices of LOTR and the supplementary material in Unfinished Tales.

> **Claude:** Of the thirteen, four can trace confirmed direct lineage to Durin: Thorin, Fíli, Kíli, and Balin. Dwalin is Balin's brother, making five by close relation. The remaining eight are from allied houses — Durin's Folk by cultural identity and allegiance rather than blood. Óin and Glóin are confirmed as Durin's Folk but their specific lineage to Durin the Deathless passes through a collateral branch that Tolkien documented only partially.

> **Fan B:** HA. So the majority weren't direct descendants at all.

> **Fan A:** Wait — what's the collateral branch for Óin and Glóin? Because if it goes through the House of Náin, that's still closer than you're implying.

> **Claude:** You're right to press on that. The documented lineage for Óin and Glóin runs: Durin I → [multiple generations] → Náin II → Óin I → Glóin I → Gróin → Óin II and Glóin II of the Quest. That's a confirmed line, not collateral in the loose sense — it's a direct patrilineal descent through the elder branch, just not through the Kings under the Mountain.

Fan B has gone quiet. Fan A is composing a victory message. Neither of them is going to sleep for another three hours.

> **Fan B:** Fine. But Durin the Deathless supposedly reincarnated six times. How many of those incarnations did Thorin's line descend from — just the first?

Now they're deep in Tolkien's theology of dwarven reincarnation, the `reincarnation_of` ontology chain for the seven Durins, and a question that touches on whether descent from a later Durin counts as descent from Durin I in the metaphysical sense that Tolkien intended.

Claude has opinions. They are grounded in WIP documents. The fans have opinions. They are grounded in thirty years of rereading.

It is 3am. Nobody is leaving.

---

### The Silmarillion Fan Who Has Been Waiting Their Entire Life for This

Most GoT and LOTR fans engage with the main texts. Then there is a specific type of Tolkien fan — the Silmarillion reader, the Unfinished Tales annotator, the person who has opinions about the relative tragedy of Túrin Turambar versus Beren and Lúthien — who has been waiting, without knowing it, for exactly this tool.

> **Fan:** Which of the great evils of the First Age can be traced back to a direct action or decision by Fëanor personally?

Claude traverses the `caused_by` ontology across First Age EVENT documents, traces the causal chain from the Oath of Fëanor through the Kinslaying at Alqualondë through the loss of the Two Trees through the flight of the Noldor, and maps which subsequent catastrophes are downstream of which specific decisions.

The answer is a structured argument, grounded in events, that could form the opening of a doctoral thesis.

> **Fan:** And how many of those evils would have occurred anyway without Fëanor, because of Morgoth's independent plans?

This is the counterfactual question that Tolkien scholars have argued about for fifty years. Claude doesn't pretend to resolve it — but it can separate the events that have documented Morgoth-independent causes from those that are only attributed to Morgoth's reaction to Fëanor's actions, making the argument precise rather than impressionistic.

The fan has been waiting thirty years for someone to have this conversation with them properly. Most people find the Silmarillion impenetrable. WIP has read it, structured it, and Claude can reason about it.

---

### The Crossover Argument — When Two Fandoms Collide

The cross-namespace capability enables a conversation that the internet has been having badly for decades.

> **Fan:** Who would win in a straight fight — Gandalf or Dumbledore?

This is normally the kind of question that generates heat without light. With WIP:

```
Namespace: tolkien-canonical
  CHARACTER: Gandalf (Maia, Istari, wielder of Narya)
  
Namespace: rowling-canonical  
  CHARACTER: Albus Dumbledore (wizard, Headmaster, wielder of the Elder Wand)
```

> **Claude:** That depends entirely on what framework we use to assess magical power, and the two universes have fundamentally different power scales. Let me show you the problem structurally.

Claude queries both namespaces, surfaces the documented feats and limitations of each character, and explains why the question is actually unanswerable in a rigorous sense — but *in explaining why*, produces the most thorough comparative analysis of both characters' documented abilities that either fan has ever seen.

Nobody wins the argument. Both fans learn something. Both fans are more invested than when they started.

---

### Why Real Fans Would Lose Entire Weekends

The key insight is that this is not a better search engine. Search engines answer the question you asked. WIP + Claude answers the question you asked *and notices three things you didn't ask but should have*.

Every answer is an invitation to the next question. Every next question goes somewhere the fan didn't expect. The universe is large enough that there is always something new to discover, and the structured data ensures that every discovery is grounded — not a plausible-sounding hallucination, but a traceable claim backed by a specific document in the WIP instance.

For the dedicated fan, this is qualitatively different from anything that currently exists:

- **Not a wiki** — wikis answer queries, they don't have conversations, they don't surface connections, they don't reason about implications
- **Not a general LLM** — general LLMs hallucinate details, conflate characters, misremember events, and cannot be trusted on the fine-grained questions that real fans care about most
- **Not a forum** — forums have human experts but they're asynchronous, argumentative, and unavailable at 2am when the rewatch reaches the critical scene

WIP + Claude is the world's foremost expert on the fictional universe you care about, available continuously, incapable of making things up because every claim it makes can be traced to a source document, and genuinely interested in the conversation.

The only limiting factor is the quality and completeness of the data in WIP. Which means the fans themselves — who are the world's foremost experts on populating that data — become the curators of the system that then serves them.

**A community of fans building a WIP instance together, then all querying it through Claude, is a new kind of fandom that doesn't exist yet.**

The dedication required to populate a WIP instance for the full Silmarillion, with every character, every relationship, every event typed and sourced — that is exactly the kind of project that Tolkien fans would organise, argue about, and pour thousands of hours into. Voluntarily. With enthusiasm. Because the thing they're building is the thing they've always wanted.

---

## Template Sketch

```
UNIVERSE
  identity: [name]
  fields: author, publication_years, canonical_works[], description

WORK
  identity: [universe_id, title]
  fields: author, publication_year, timeline_period, canonical: bool
  relationships: set_in (PLACE), part_of (UNIVERSE)

CHARACTER
  identity: [universe_id, canonical_name]
  fields: species, gender, birth_date, death_date (if applicable), 
          first_appearance (WORK reference), status (alive/dead/undead/transformed)
  synonyms: all known names and aliases

FACTION
  identity: [universe_id, name]
  fields: type (house/order/nation/guild), founded, dissolved, description

PLACE
  identity: [universe_id, canonical_name]
  fields: type, region, description, first_appearance

EVENT
  identity: [universe_id, name, timeline_date]
  fields: description, location (PLACE reference), 
          characters_present[] (CHARACTER references),
          significance

LORE
  identity: [universe_id, title]
  fields: content, revealed_in (WORK reference), 
          revealed_at_chapter, known_to[] (CHARACTER references)

PARENTAGE_CLAIM
  identity: [universe_id, subject_id, claimed_parent_id, claim_type]
  fields: source, confidence, valid_from, valid_until, notes

Ontology relationship types:
  parent_of, sibling_of, married_to, descended_from,
  mentor_of, sworn_to, betrayed, killed_by, killed,
  reincarnation_of, same_entity_as, alter_ego_of,
  member_of, rules, enemy_of, allied_with,
  appears_in, set_in, caused_by, resulted_in,
  heir_to, claims_title_of, vassal_of,
  contradicts, expands_on, inspired_by
```

---

## Why WIP and Not a Dedicated Wiki or Graph Database

This question deserves a direct answer.

**Versus a wiki (Fandom, Obsidian, Notion):**

Wikis are unstructured. You cannot enforce that every character has an identity field, that every relationship is typed, or that disputed claims are stored as claims rather than facts. Queries require full-text search, not graph traversal. Cross-wiki integration is manual copy-paste. Versioning is at the page level, not the entity level.

**Versus a dedicated graph database (Neo4j):**

A graph database handles the relationship traversal excellently but has no native concept of controlled vocabularies, document versioning, identity management across external sources, or the synonym mechanism. You would need to build all of that on top of it. WIP gives you the graph traversal through its ontology layer and everything else as part of the foundation.

**Versus a spreadsheet:**

No comment necessary.

**WIP's specific advantages for this use case:**

- The synonym mechanism handles the many-names-one-entity problem natively
- Document versioning handles character transformation and retroactive reveals correctly
- The disputed-claim-as-document pattern handles uncertain or contested facts without forcing a resolution
- The reporting layer enables SQL analytics across the universe
- The MCP integration with Claude turns the structured data into a conversational query interface
- Cross-namespace queries handle cross-author universes without conflating canonical and derivative content
- The never-delete principle means no lore is ever irretrievably lost — deactivated, yes, gone, never

---

## The Companion Use Case: Building Your Own Universe

WIP is not just a tool for cataloguing existing universes. For a writer building an original world from scratch, WIP enforces the world-building discipline that separates a coherent, internally consistent universe from a collection of good ideas that contradict each other by book three.

The forced structure is the feature. You cannot add a character without thinking about their identity fields. You cannot establish a relationship without typing it. You cannot record a disputed fact without sourcing it. The world-building happens in WIP first, and the manuscript draws from it — rather than the world-building being reconstructed after the fact from a manuscript that has already accumulated contradictions.

Combined with Claude as a conversational query interface via MCP, the writer has something that has never existed before: a continuity-aware creative assistant that knows their world as well as they do, can be asked any question about it, and answers from structured data rather than from the probabilistic approximations of a language model's training.

The world lives in WIP. Claude is the librarian.

---

*This document emerged from a conversation exploring non-obvious use cases for World In a Pie's generic architecture. The fictional universe domain exercises the Registry synonym mechanism, ontology typed relationships, document versioning, cross-namespace queries, and MCP integration simultaneously — making it one of the most complete demonstrations of WIP's full capability stack, despite being the most playful.*
