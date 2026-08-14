
from app.agents.registry import DEFAULT_REGISTRATIONS, AgentRegistry
from app.domain import AgentRegistration


def test_registry_loads_defaults():
    registry = AgentRegistry()
    assert registry.get("fact_checker") is not None
    assert registry.get("company_analyst") is not None
    assert registry.get("skeptic") is not None
    assert registry.get("synthesizer") is not None
    assert registry.get("planner") is not None
    assert registry.get("retriever") is not None
    assert registry.get("impact_analyst") is not None


def test_registry_find_by_capability():
    registry = AgentRegistry()
    results = registry.find(capabilities=["company_analyze"])
    assert any(r.agent_key == "company_analyst" for r in results)


def test_registry_find_by_output_schema():
    registry = AgentRegistry()
    results = registry.find(output_schema_ref="synthesis-output/1.0.0")
    assert len(results) == 1
    assert results[0].agent_key == "synthesizer"


def test_registry_allowed_tools():
    registry = AgentRegistry()
    assert "calculate_financial_metrics" in registry.allowed_tools_for("company_analyst")
    assert registry.allowed_tools_for("unknown") == []


def test_registry_to_tool_gateway_whitelist():
    registry = AgentRegistry()
    whitelist = registry.to_tool_gateway_whitelist()
    assert "company_analyst" in whitelist
    assert "calculate_financial_metrics" in whitelist["company_analyst"]


def test_registry_register_override():
    registry = AgentRegistry()
    custom = AgentRegistration(
        agent_key="custom_agent",
        version="1.0.0",
        display_name="Custom",
        capabilities=["custom"],
        input_schema_refs=["input/1.0.0"],
        output_schema_ref="output/1.0.0",
        allowed_tools=["tool_a"],
    )
    registry.register(custom)
    assert registry.get("custom_agent") is not None


def test_default_registrations_cover_required_agents():
    keys = {r.agent_key for r in DEFAULT_REGISTRATIONS}
    assert {
        "fact_checker",
        "company_analyst",
        "skeptic",
        "synthesizer",
        "planner",
        "retriever",
        "impact_analyst",
    }.issubset(keys)
