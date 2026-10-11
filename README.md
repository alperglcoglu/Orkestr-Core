# Orkestr

Orkestr is an open-source Claude Code plugin that runs your software project like a small autonomous AI company: it plans, holds bounded meetings with decision records, builds tasks in safe parallel waves, reviews work against real evidence, and reports status and retrospectives. Everything lives in local files; no paid services are added.

- Plugin docs and installation: [plugins/orkestr/README.md](plugins/orkestr/README.md)
- Vision and full specification: [MASTER_SPEC.md](MASTER_SPEC.md)
- Phase 1 implementation plan: [PHASE1_IMPLEMENTATION.md](PHASE1_IMPLEMENTATION.md)

## Quick install

Inside Claude Code:

```
/plugin marketplace add alperglcoglu/Orkestr
/plugin install orkestr@orkestr
```

Then, in the project you want to manage:

```
/orkestr:run A simple to-do list app; free tools only; with tests
```

## License

[MIT](LICENSE)
