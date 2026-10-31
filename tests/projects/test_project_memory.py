"""Project memory: retrieval scope and deletion semantics."""

from __future__ import annotations

from uuid import uuid4

from project_helpers import ProjectEnv

from yoreni.projects.scope import GLOBAL_SCOPE


async def _hit_ids(env: ProjectEnv, query: str, scope: str) -> set[str]:
    return {hit.fact.id for hit in await env.memory.search(query, scope=scope)}


async def test_retrieval_follows_the_conversation_scope(env: ProjectEnv):
    alpha = env.create("Alpha")
    beta = env.create("Beta")
    global_run = env.turn("observatory notes")
    alpha_run = env.turn("alpha notes", scope=alpha.scope)
    beta_run = env.turn("beta notes", scope=beta.scope)
    global_fact = await env.fact(
        global_run, "The observatory tracks quasar spectra nightly.",
        scope=GLOBAL_SCOPE, subject="observatory", predicate="tracks",
    )
    alpha_fact = await env.fact(
        alpha_run, "Project Alpha measures quasar brightness.",
        scope=alpha.scope, subject="alpha", predicate="measures",
    )
    beta_fact = await env.fact(
        beta_run, "Project Beta models quasar lensing.",
        scope=beta.scope, subject="beta", predicate="models",
    )

    assert await _hit_ids(env, "quasar", alpha.scope) == {global_fact, alpha_fact}
    assert await _hit_ids(env, "quasar", beta.scope) == {global_fact, beta_fact}
    assert await _hit_ids(env, "quasar", GLOBAL_SCOPE) == {global_fact}


async def test_delete_removes_project_memory_and_keeps_global_and_main_chat(env: ProjectEnv):
    thesis = env.create("Thesis")
    other = env.create("Other")
    main_message = uuid4().hex
    main_run = env.turn("main chat question", message_id=main_message, answer="main answer")
    thesis_run = env.turn("thesis notes", scope=thesis.scope, answer="thesis answer")
    other_run = env.turn("other notes", scope=other.scope, answer="other answer")

    main_fact = await env.fact(
        main_run, "The user keeps a paper notebook.",
        scope=GLOBAL_SCOPE, subject="user", predicate="notebook",
    )
    project_fact = await env.fact(
        thesis_run, "The thesis compares spectral clustering methods.",
        scope=thesis.scope, subject="thesis", predicate="method",
    )
    promoted_fact = await env.fact(
        thesis_run, "The user typesets papers in LaTeX.",
        scope=GLOBAL_SCOPE, subject="user", predicate="typesetting",
    )
    other_fact = await env.fact(
        other_run, "The other project studies graph cuts.",
        scope=other.scope, subject="other", predicate="topic",
    )
    env.memory.record_run(
        thesis_run, [{"role": "user", "content": "scratch observation"}],
        scope=thesis.scope,
    )
    evidence = {item.content: item.id for item in env.memory.store.evidence_for_run(thesis_run)}
    promoted_evidence = env.memory.store.sources_for([promoted_fact])[promoted_fact][0].evidence_id

    result = await env.projects.delete(thesis.id)

    assert result["memory"]["facts"] == 1
    assert env.memory.store.fact(project_fact) is None
    assert env.memory.store.fact(main_fact) is not None
    assert env.memory.store.fact(promoted_fact) is not None
    assert env.memory.store.fact(other_fact) is not None
    assert env.memory.store.evidence([promoted_evidence])[promoted_evidence].content == (
        "The user typesets papers in LaTeX."
    )
    assert env.memory.store.evidence([evidence["scratch observation"]]) == {}
    assert env.memory.store.evidence([evidence["The user typesets papers in LaTeX."]])

    assert [run["content"] for run in env.store.webui_history(scope=GLOBAL_SCOPE)] == [
        "main chat question",
    ]
    assert {row["text"] for row in env.store.conversation_rows(scope=GLOBAL_SCOPE)} == {
        "main chat question", "main answer",
    }
    assert env.store.resolve_message_reference(main_message) is not None
    assert env.store.webui_history(scope=other.scope)[0]["content"] == "other notes"
