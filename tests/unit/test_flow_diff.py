"""Unit tests for structural flow JSON diff."""

from nifi_mcp_server.flow_diff import diff_flow_documents


def _minimal_flow(name: str, processors=None, connections=None, child_groups=None):
    return {
        "flowEncodingVersion": "1.0",
        "flowContents": {
            "identifier": "root-id",
            "name": name,
            "position": {"x": 0, "y": 0},
            "processors": processors or [],
            "connections": connections or [],
            "controllerServices": [],
            "inputPorts": [],
            "outputPorts": [],
            "funnels": [],
            "labels": [],
            "processGroups": child_groups or [],
            "remoteProcessGroups": [],
        },
    }


def _proc(ident: str, name: str, prop_value: str = "a", x: float = 1.0):
    return {
        "identifier": ident,
        "instanceIdentifier": f"inst-{ident}",
        "name": name,
        "type": "org.apache.nifi.processors.standard.LogMessage",
        "componentType": "PROCESSOR",
        "position": {"x": x, "y": 2.0},
        "properties": {"logLevel": prop_value},
        "propertyDescriptors": {"logLevel": {"name": "logLevel", "displayName": "Log Level"}},
        "style": {},
    }


def test_identical_when_only_positions_differ():
    left = _minimal_flow("Root", processors=[_proc("p1", "Log", "INFO", x=0)])
    right = _minimal_flow("Root", processors=[_proc("p1", "Log", "INFO", x=999)])
    result = diff_flow_documents(left, right)
    assert result["identical"] is True
    assert result["summary"]["total"] == 0


def test_detects_property_change():
    left = _minimal_flow("Root", processors=[_proc("p1", "Log", "INFO")])
    right = _minimal_flow("Root", processors=[_proc("p1", "Log", "WARN")])
    result = diff_flow_documents(left, right)
    assert result["identical"] is False
    assert result["summary"]["changed"] == 1
    change = result["changes"][0]
    assert change["kind"] == "changed"
    assert "properties.logLevel" in change["fields"]


def test_detects_added_and_removed_by_stable_key():
    left = _minimal_flow(
        "Root",
        processors=[_proc("id-old", "Keep", "a"), _proc("id-gone", "Gone", "a")],
    )
    right = _minimal_flow(
        "Root",
        processors=[_proc("id-new-keep", "Keep", "a"), _proc("id-added", "Added", "a")],
    )
    # Same name "Keep" matches by stable key despite different identifiers
    result = diff_flow_documents(left, right)
    kinds = {(c["kind"], c.get("name")) for c in result["changes"]}
    assert ("removed", "Gone") in kinds
    assert ("added", "Added") in kinds
    assert result["summary"]["removed"] == 1
    assert result["summary"]["added"] == 1


def test_nested_process_group_diff():
    left = _minimal_flow(
        "Root",
        child_groups=[
            {
                "identifier": "child-1",
                "name": "Child",
                "position": {"x": 0, "y": 0},
                "processors": [_proc("p1", "Inner", "old")],
                "connections": [],
                "controllerServices": [],
                "inputPorts": [],
                "outputPorts": [],
                "funnels": [],
                "labels": [],
                "processGroups": [],
                "remoteProcessGroups": [],
            }
        ],
    )
    right = _minimal_flow(
        "Root",
        child_groups=[
            {
                "identifier": "child-1",
                "name": "Child",
                "position": {"x": 50, "y": 50},
                "processors": [_proc("p1", "Inner", "new")],
                "connections": [],
                "controllerServices": [],
                "inputPorts": [],
                "outputPorts": [],
                "funnels": [],
                "labels": [],
                "processGroups": [],
                "remoteProcessGroups": [],
            }
        ],
    )
    result = diff_flow_documents(left, right)
    assert result["identical"] is False
    assert any(
        c["kind"] == "changed" and "Child" in (c.get("path") or "") for c in result["changes"]
    )


def test_connection_match_by_endpoints():
    conn_left = {
        "identifier": "c-old",
        "name": "",
        "componentType": "CONNECTION",
        "position": {"x": 0, "y": 0},
        "source": {"name": "A", "type": "PROCESSOR"},
        "destination": {"name": "B", "type": "PROCESSOR"},
        "selectedRelationships": ["success"],
        "backPressureObjectThreshold": 10000,
    }
    conn_right = {
        "identifier": "c-new",
        "name": "",
        "componentType": "CONNECTION",
        "position": {"x": 10, "y": 10},
        "source": {"name": "A", "type": "PROCESSOR"},
        "destination": {"name": "B", "type": "PROCESSOR"},
        "selectedRelationships": ["success"],
        "backPressureObjectThreshold": 20000,
    }
    left = _minimal_flow("Root", connections=[conn_left])
    right = _minimal_flow("Root", connections=[conn_right])
    result = diff_flow_documents(left, right)
    assert result["summary"]["added"] == 0
    assert result["summary"]["removed"] == 0
    assert result["summary"]["changed"] == 1
    assert "backPressureObjectThreshold" in result["changes"][0]["fields"]
