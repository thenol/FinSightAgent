from app.capabilities import (
    CapabilityPackError,
    compile_capability_plan,
    default_capability_registry,
)


def test_equity_financing_pack_resolves_and_compiles_verified_plan() -> None:
    registry = default_capability_registry()
    pack = registry.resolve_for_event("equity_financing", subtype="share_placement")

    assert pack is not None
    assert pack.manifest.pack_id == "capital-markets.equity-financing"
    plan = compile_capability_plan(
        pack,
        extracted_fields={
            "issuer": "阿里巴巴",
            "financing_method": "share_placement",
            "announcement_stage": "subscribed",
            "use_of_proceeds": "AI建设",
        },
        verified=True,
    )
    assert plan.phase == "verified"
    assert "build_impact_graph" in [task.name for task in plan.tasks]


def test_pack_lifecycle_rejects_invalid_transition() -> None:
    registry = default_capability_registry()
    try:
        registry.transition("capital-markets.equity-financing", "1.0.0", "candidate")
    except CapabilityPackError:
        pass
    else:
        raise AssertionError("active pack must not transition directly to candidate")
