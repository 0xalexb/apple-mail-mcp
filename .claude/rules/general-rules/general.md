# General Rules

## Workflow

- If connected to IDE, always check diagnostics for errors and warnings.
- Act, then report. Do not end a turn asking permission to continue work that is
  already in scope — finish it and say what was done.
- Plan first only when the work is non-trivial: a new component, a change
  spanning several files, or a choice with more than one defensible answer. A
  single fix, a step already agreed this session, or its obvious follow-through
  is not that.

### Do without asking

- Creating, editing, or deleting files inside the current repository.
- Running tests, and reading anything.
- `git add`, `git commit`, and `git push` of a branch with no upstream.

### Always stop and ask

- Rewriting published history: `push --force` / `--force-with-lease`, `rebase`,
  `commit --amend`, or `reset` on a branch that has an upstream.
- Deleting or modifying tests.
- Writing anything outside the current repository.
- Any target carrying `prod` or `production`, and any shared or remote system.
- Deleting data that no version control holds.

## File Format

- Always add a newline at the end of every file.

## Process

- Do not run linters unless explicitly instructed.
- Do not compile code unless explicitly instructed.
- Never run `make` unless explicitly instructed.
- Never write tests for database queries.

## tools

- Use UV for python projects.
