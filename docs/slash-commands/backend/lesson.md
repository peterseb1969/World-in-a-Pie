Capture a lesson learned and encode it into the gene pool. Use this whenever Peter or a YAC discovers something that future agents should know.

### Usage

`/lesson <text>` — record a lesson with the given text
`/lesson` — infer the lesson from current conversation context

### Steps

#### 1. Get the current time

```bash
date '+%Y-%m-%d %H:%M'
```

#### 2. Determine the lesson text

If the user provided an argument, use it as the lesson. Otherwise, infer from the current conversation — what was just discovered, corrected, or decided that a future agent needs to know.

#### 3. Categorize the lesson

Assign one category:

| Category | When to use |
|----------|-------------|
| `dependency` | Version pins, install order, package conflicts |
| `api` | Endpoint behavior, resolution, payload requirements |
| `testing` | Test infrastructure, mock patterns, CI behavior |
| `tooling` | Shell, venv, paths, environment setup |
| `workflow` | Agent behavior, process, handoff patterns |
| `platform` | WIP-specific behavior, PoNIFs, conventions |

#### 4. Append to the lessons file

Append to `/Users/peter/Development/FR-YAC/lessons.md`:

If the file doesn't exist, create it with this header:

```markdown
# Lessons Learned

Encoded lessons from the WIP Constellation Experiment. These are facts discovered through practice that future agents and gene pool updates should incorporate.

| Date | Category | Lesson | Source |
|------|----------|--------|--------|
```

Then append one row:

```markdown
| <YYYY-MM-DD> | <category> | <lesson text — one line, concise, actionable> | <session ID or "Peter"> |
```

#### 5. Confirm

Tell Peter what was recorded. One line.

### When to use this

- After discovering a dependency pin, version conflict, or install order issue
- After a bug caused by an assumption that turned out wrong
- After Peter corrects an agent's approach in a way that applies broadly
- After a PoNIF encounter that should be documented
- Whenever Peter says "remember this" or "lesson learned" or similar

### What NOT to record

- Things already in CLAUDE.md (check first)
- One-off task details that won't recur
- Opinions or preferences (use memory for those)

### Gene Pool Integration

The lessons file is a staging area. Periodically, lessons should be reviewed and the important ones incorporated into:
- CLAUDE.md files (for all agents)
- Setup script heredocs (for specific agent types)
- Slash command updates (for specific workflows)

This review is a human task — lessons don't auto-propagate. The file makes them findable.
