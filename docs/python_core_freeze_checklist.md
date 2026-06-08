# Python Core Freeze Checklist

## Knowledge ingestion/indexing

- [ ] Office structure parsing works
- [ ] segment schema stable
- [ ] embedding manifest stable
- [ ] Qdrant payload fields documented
- [ ] local data/artifacts ignored

## Form filling

- [ ] Step15AgentRunner overlay mode documented
- [ ] raw prediction immutable
- [ ] overlay additive
- [ ] review queue generated
- [ ] writeback gated by overlay
- [ ] checkpoint/resume works
- [ ] run_manifest.json generated
- [ ] artifact contracts documented

## Go handoff

- [ ] stable CLI command
- [ ] stable out_dir layout
- [ ] run_manifest.json available
- [ ] validate-artifacts available
- [ ] no Python internal APIs required by Go
