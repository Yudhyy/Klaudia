# Agent skills

`agent_skills.registry.SkillRegistry` lists versioned procedures and loads
their text by a fixed registry name. The wheel contains the registry and all
procedure Markdown files. It depends on Pydantic and has no runtime, model
provider or application dependency.

Procedure names, versions, descriptions and content bytes stay stable during
this extraction. The agent runtime retains its prompt and consumes this registry.
The packaging suite installs the skills alone and compares each procedure with
the saved release fixture.
