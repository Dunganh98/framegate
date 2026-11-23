# Coding guidelines

- The smallest diff that meets the spec.
- No speculative options, helpers or abstractions: nothing the spec does not need today.
- Comments: at most 4 lines, and only for what the code cannot say.
- Never change behaviour to satisfy a tool (lint, formatter, coverage): restore the original
  line instead.
- Match the style of the surrounding code.
