from ems.application.context import ApplicationContext


def test_application_context_preserves_dependencies_and_named_state():
    source = object()
    settings = {"control.dry_run": True}
    state = {"ready": True}
    ctx = ApplicationContext(source=source, settings=settings, runtime_state=state)
    assert ctx.source is source
    assert ctx.settings is settings
    assert ctx.runtime_state is state
    assert isinstance(ctx.control_state, dict)
    assert isinstance(ctx.background_tasks, dict)

