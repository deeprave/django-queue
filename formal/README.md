# Formal protocol assessment

This directory holds optional formal models and the unregistered agent skills
used to create, check, and maintain them. OpenSpec remains the human-readable,
normative product contract. A TLA+ model is a deliberately bounded executable
model of selected concurrent requirements, not a substitute for that contract.

Install the skills only when an agent environment should discover them:

```sh
sh formal/install-agent-skills
```

The default target is `~/.agents/skills`. Use `--target PATH` for a specific
agent's skill directory. See [agents/README.md](agents/README.md) for the
operator workflow and [agents/skills](agents/skills) for the skill sources.

When TLA+ Toolbox is installed locally, run a model with its bundled Java
runtime and TLC tools:

```sh
formal/run-tlc -config formal/tla/<protocol>.cfg formal/tla/<protocol>.tla
```

Set `TLA_TOOLBOX_APP` if the Toolbox application is not installed at
`/Applications/TLA+ Toolbox.app`.
