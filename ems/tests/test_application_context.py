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



def test_context_exposes_typed_collaborators_without_runtime_dictionary_lookup():
    from typing import get_type_hints

    from ems.application.protocols import DiagnosticsProvider, FinanceProvider, PlanProvider

    hints = get_type_hints(ApplicationContext)
    assert hints["current_plan"] == PlanProvider | None
    assert hints["diagnostics_snapshot"] == DiagnosticsProvider | None
    assert hints["finance_window"] == FinanceProvider | None
