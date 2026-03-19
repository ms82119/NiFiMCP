# Token Cost Tracking and Metrics

## Overview

The documentation workflow now tracks token usage and costs throughout all phases, providing detailed metrics for evaluation and tuning.

## Configuration

### Property Extraction Mode

Users can configure the property extraction mode in `config.yaml`:

```yaml
documentation_workflow:
  analysis:
    property_extraction:
      mode: "balanced"  # Options: "minimal", "balanced", "comprehensive"
      max_properties_per_processor: 10
      truncate_large_values: true
      max_value_length: 500
      include_defaults: false
```

**Modes:**
- **minimal**: Only heuristic extraction (most token-efficient)
- **balanced**: Heuristics + processor templates (default, recommended)
- **comprehensive**: Heuristics + templates + fallback to all properties (most complete)

### Generation Shared State Saving

Users can control whether to save generation shared state snapshots:

```yaml
documentation_workflow:
  output:
    save_shared_state: true  # Save generation_shared_state_{request_id}.json
```

## Token Cost Tracking

### Per-Phase Tracking

Token costs are tracked separately for each phase:

```json
{
  "metrics": {
    "token_costs": {
      "discovery": {
        "tokens_in": 0,
        "tokens_out": 0,
        "cost_usd": 0.0
      },
      "analysis": {
        "tokens_in": 15234,
        "tokens_out": 3456,
        "cost_usd": 0.023456
      },
      "generation": {
        "tokens_in": 8765,
        "tokens_out": 1234,
        "cost_usd": 0.014567
      },
      "total": {
        "tokens_in": 23999,
        "tokens_out": 4690,
        "cost_usd": 0.038023
      }
    }
  }
}
```

### Provider-Specific Pricing

Token costs are calculated using provider-specific pricing:

**OpenAI:**
- `gpt-4o-mini`: $0.15/$0.60 per 1M tokens (in/out)
- `gpt-4o`: $2.50/$10.00 per 1M tokens (in/out)
- `gpt-4-turbo`: $10.00/$30.00 per 1M tokens (in/out)
- `gpt-3.5-turbo`: $0.50/$1.50 per 1M tokens (in/out)

**Anthropic:**
- `claude-3-5-sonnet`: $3.00/$15.00 per 1M tokens (in/out)
- `claude-3-opus`: $15.00/$75.00 per 1M tokens (in/out)
- `claude-3-haiku`: $0.25/$1.25 per 1M tokens (in/out)

**Gemini:**
- `gemini-1.5-pro`: $1.25/$5.00 per 1M tokens (in/out)
- `gemini-1.5-flash`: $0.075/$0.30 per 1M tokens (in/out)

**Perplexity:**
- `pplx-70b-online`: $0.70/$0.70 per 1M tokens (in/out)

### Cost Calculation

Costs are calculated automatically in `AsyncNiFiWorkflowNode._calculate_token_cost()`:

```python
cost = (tokens_in / 1_000_000 * price_in) + (tokens_out / 1_000_000 * price_out)
```

If provider/model pricing is unknown, defaults to $1.00/$3.00 per 1M tokens (in/out).

## Shared State Snapshots

### Discovery Phase

Saved to: `logs/debug/discovery_shared_state_{request_id}.json`

Contains:
- `flow_graph`: All discovered processors, connections, PGs, ports
- `pg_tree`: Hierarchical structure
- `metrics.discovery`: Discovery phase metrics

### Analysis Phase

Saved to: `logs/debug/analysis_shared_state_{request_id}.json`

Contains:
- All discovery data
- `pg_summaries`: All process group summaries
- `virtual_groups`: Virtual group summaries (if any)
- `metrics.analysis`: Analysis phase metrics
- `metrics.token_costs.analysis`: Token costs for analysis phase

### Generation Phase

Saved to: `logs/debug/generation_shared_state_{request_id}.json` (if enabled)

Contains:
- All analysis data
- `doc_sections`: Generated documentation sections
- `final_document`: Complete documentation
- `metrics.generation`: Generation phase metrics
- `metrics.token_costs.generation`: Token costs for generation phase
- `metrics.token_costs.total`: Total token costs across all phases

## Metrics Structure

Complete metrics structure in shared state:

```json
{
  "metrics": {
    "workflow_start_time": 1234567890.123,
    "total_duration_ms": 45000,
    
    "discovery": {
      "api_calls": 3,
      "total_processors": 32,
      "total_connections": 40,
      "total_process_groups": 6,
      "total_duration_ms": 27198
    },
    
    "analysis": {
      "pgs_analyzed": 5,
      "virtual_groups_created": 0,
      "llm_calls": 5
    },
    
    "generation": {
      "pgs_documented": 5,
      "document_size_bytes": 15234,
      "sections_generated": 5
    },
    
    "token_costs": {
      "discovery": {
        "tokens_in": 0,
        "tokens_out": 0,
        "cost_usd": 0.0
      },
      "analysis": {
        "tokens_in": 15234,
        "tokens_out": 3456,
        "cost_usd": 0.023456
      },
      "generation": {
        "tokens_in": 8765,
        "tokens_out": 1234,
        "cost_usd": 0.014567
      },
      "total": {
        "tokens_in": 23999,
        "tokens_out": 4690,
        "cost_usd": 0.038023
      }
    }
  }
}
```

## Usage for Evaluation and Tuning

### 1. Compare Extraction Modes

Run the same flow with different `property_extraction.mode` settings and compare:
- Token usage per phase
- Total cost
- Documentation quality

**Example:**
```bash
# Run with minimal mode
# Check logs/debug/generation_shared_state_*.json
# Note: token_costs.total.cost_usd

# Run with balanced mode
# Compare token costs and documentation quality

# Run with comprehensive mode
# Compare token costs vs. documentation completeness
```

### 2. Identify Token Hotspots

Check `token_costs` per phase to identify where most tokens are used:
- High `analysis` costs → Too many properties extracted?
- High `generation` costs → Executive summary or diagram too verbose?

### 3. Optimize Property Extraction

Use token costs to tune:
- `max_properties_per_processor`: Reduce if analysis costs are high
- `max_value_length`: Truncate long values if needed
- `truncate_large_values`: Enable to reduce token usage

### 4. Cost Budgeting

Set token budgets per phase based on historical data:
```yaml
analysis:
  max_tokens_per_analysis: 8000  # Adjust based on token_costs.analysis
```

## Accessing Metrics

### Via API

Metrics are included in workflow completion response:

```python
result = await workflow_executor.execute_async(initial_context)
metrics = result.get("metrics", {})
token_costs = metrics.get("token_costs", {})
total_cost = token_costs.get("total", {}).get("cost_usd", 0.0)
```

### Via Shared State Snapshots

Load and analyze shared state snapshots:

```python
import json

with open("logs/debug/generation_shared_state_{request_id}.json") as f:
    shared_state = json.load(f)
    
token_costs = shared_state["metrics"]["token_costs"]
print(f"Total cost: ${token_costs['total']['cost_usd']:.6f}")
print(f"Analysis: {token_costs['analysis']['tokens_in']} in, "
      f"{token_costs['analysis']['tokens_out']} out")
```

## Configuration Accessors

Use configuration accessors in code:

```python
from config.settings import (
    get_doc_property_extraction_mode,
    get_doc_property_extraction_config,
    should_save_generation_shared_state
)

mode = get_doc_property_extraction_mode()  # "minimal", "balanced", "comprehensive"
config = get_doc_property_extraction_config()
save_state = should_save_generation_shared_state()
```

## Future Enhancements

1. **Cost Budget Alerts**: Warn when costs exceed thresholds
2. **Historical Cost Tracking**: Store costs in database for trend analysis
3. **Cost Optimization Suggestions**: Recommend mode changes based on usage patterns
4. **Provider Cost Comparison**: Show cost differences between providers/models

