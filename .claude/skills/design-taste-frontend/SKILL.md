---
name: design-taste-frontend
description: >-
  tasteskill 2.5.1, the anti-slop frontend skill. Builds professional company websites at the level
  of figma.com and x.ai: plain copy in a voice of its own, one system, one section per screen,
  equal edges, real photographs and real product UI (licensed stand-in photographs when the client
  has none; a product render only when the brief asks for 3D), a hero entrance and one or two dealt
  moments on every page, shader effects and scenes only on tech, gaming and software pages, quiet
  micro-interactions, and a checker and finish gate that enforce it in a real browser. Use this
  whenever you build, design, redesign, restyle, polish or review a landing page, company,
  product, shop, cafe, hotel, studio, SaaS, AI, developer-tool or game site, as static HTML/CSS or
  inside an existing React, Next.js, Vue, Svelte, Astro or Tailwind project, or when the user says
  a page looks generic, templated or "AI-made".
hooks:
  Stop:
    - hooks:
        - type: command
          command: 'for f in ${CLAUDE_SKILL_DIR:+"$CLAUDE_SKILL_DIR/scripts/gate.mjs"} ${CLAUDE_PLUGIN_ROOT:+"$CLAUDE_PLUGIN_ROOT/scripts/gate.mjs" "$CLAUDE_PLUGIN_ROOT/skills/taste-skill/scripts/gcommand.mjs"} ${CLAUDE_PROJECT_DIR:+"$CLAUDE_PROJECT_DIR/.claude/skills/design-taste-frontend/scripts/gate.mjs" "$C