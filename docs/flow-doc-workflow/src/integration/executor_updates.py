"""
AsyncWorkflowExecutor Updates for Multi-Node Workflows

These updates ensure that configuration is properly propagated to all nodes
in a multi-node workflow, not just the start node.

Apply these changes to: nifi_mcp_server/workflows/core/async_executor.py
"""

from typing import Dict, Any, List, Set


def set_config_updated(self, config_dict: Dict[str, Any]):
    """
    Set config dictionary to ALL nodes in the workflow.
    
    This updated version traverses the entire workflow graph to ensure
    all nodes receive configuration, not just the start node.
    
    Args:
        config_dict: Configuration dictionary to pass to nodes
    """
    self._config_dict = config_dict
    self.bound_logger.info("Config set in async executor")
    
    # Debug: Log workflow structure
    self.bound_logger.info(f"Workflow type: {type(self.workflow)}, is_async: {self.is_async_workflow}")
    
    if self.is_async_workflow:
        # For async workflows, collect all nodes by traversing the graph
        all_nodes = self._collect_all_nodes()
        
        config_count = 0
        for node in all_nodes:
            if hasattr(node, 'set_config'):
                node.set_config(config_dict)
                config_count += 1
                self.bound_logger.debug(f"Config passed to node: {node.name}")
        
        self.bound_logger.info(f"Config distributed to {config_count}/{len(all_nodes)} nodes")
        
    elif isinstance(self.workflow, list):
        # Sync workflow - list of nodes
        for node in self.workflow:
            if hasattr(node, 'set_config'):
                node.set_config(config_dict)
                self.bound_logger.debug(f"Config passed to sync node: {node.name}")


def _collect_all_nodes(self) -> List:
    """
    Traverse workflow graph to collect all nodes.
    
    Handles:
    - Single node workflows (unguided pattern)
    - Multi-node workflows with transitions
    - Self-loops (nodes that transition back to themselves)
    
    Returns:
        List of all nodes in the workflow
    """
    visited: Set[int] = set()  # Track by id() to handle duplicate references
    nodes: List = []
    
    def traverse(node):
        """Recursive traversal with cycle detection."""
        if node is None:
            return
        
        node_id = id(node)
        if node_id in visited:
            return  # Already visited (handles self-loops)
        
        visited.add(node_id)
        nodes.append(node)
        
        # Traverse successors if the node has them
        if hasattr(node, 'successors') and node.successors:
            for action, successor in node.successors.items():
                traverse(successor)
    
    # Start traversal from the workflow's start node
    if hasattr(self.workflow, 'start_node'):
        traverse(self.workflow.start_node)
    elif hasattr(self.workflow, 'nodes'):
        # Alternative: Direct access to nodes list
        nodes = list(self.workflow.nodes)
    elif hasattr(self.workflow, '_nodes'):
        # Another alternative
        nodes = list(self.workflow._nodes)
    else:
        self.bound_logger.warning(
            f"Could not find nodes in workflow {self.workflow_name}"
        )
    
    return nodes


# ============================================================================
# Integration Instructions
# ============================================================================
#
# 1. Add _collect_all_nodes method to AsyncWorkflowExecutor class
#
# 2. Replace set_config method with set_config_updated
#    (or merge the changes into the existing method)
#
# 3. The key changes are:
#    - Call _collect_all_nodes() to find all nodes
#    - Loop through all nodes and call set_config()
#    - Handle self-loops by tracking visited node IDs
#
# Example diff for set_config:
#
# - if hasattr(self.workflow, 'start_node') and hasattr(self.workflow.start_node, 'set_config'):
# -     self.workflow.start_node.set_config(config_dict)
# + all_nodes = self._collect_all_nodes()
# + for node in all_nodes:
# +     if hasattr(node, 'set_config'):
# +         node.set_config(config_dict)
# ============================================================================

