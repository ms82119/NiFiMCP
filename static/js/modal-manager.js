/**
 * Modal Manager - Handles workflow modal functionality
 */

class ModalManager {
    constructor() {
        this.availablePhases = [];
        this.selectedPhases = new Set(['all']);
        this.init();
    }
    
    init() {
        this.setupEventListeners();
    }
    
    setupEventListeners() {
        const workflowModalBtn = document.getElementById('workflow-modal-btn');
        if (workflowModalBtn) {
            workflowModalBtn.addEventListener('click', () => this.openWorkflowModal());
        }
        
        // Add close button listeners
        const closeModalBtn = document.getElementById('close-workflow-modal-btn');
        if (closeModalBtn) {
            closeModalBtn.addEventListener('click', () => this.closeWorkflowModal());
        }
        
        const closeModalFooterBtn = document.getElementById('close-workflow-modal-footer-btn');
        if (closeModalFooterBtn) {
            closeModalFooterBtn.addEventListener('click', () => this.closeWorkflowModal());
        }
        
        // Close modal when clicking outside
        const modal = document.getElementById('workflow-modal');
        if (modal) {
            modal.addEventListener('click', (e) => {
                if (e.target === modal) {
                    this.closeWorkflowModal();
                }
            });
        }
    }
    
    openWorkflowModal() {
        const modal = document.getElementById('workflow-modal');
        if (modal) {
            modal.style.display = 'flex';
            console.log('ModalManager: Opening workflow modal');
            this.loadModalData();
        }
    }
    
    async loadModalData() {
        // Load tools first, then workflows, so we can extract phases from both
        await this.loadToolsModal();
        await this.loadWorkflowsModal();
    }
    
    closeWorkflowModal() {
        const modal = document.getElementById('workflow-modal');
        if (modal) {
            modal.style.display = 'none';
        }
    }
    
    async loadWorkflowsModal() {
        try {
            console.log('ModalManager: Loading workflows for modal');
            const response = await fetch('/api/settings/workflows');
            if (response.ok) {
                const workflows = await response.json();
                console.log('ModalManager: Received workflows:', workflows.map(w => w.name));
                this.setupWorkflowSelect(workflows);
                // Extract phases from both workflows and tools (tools should be loaded by now)
                this.extractPhases(workflows);
                this.setupPhaseFilters();
            } else {
                console.error('ModalManager: Failed to load workflows, status:', response.status);
            }
        } catch (error) {
            console.error('Error loading workflows for modal:', error);
        }
    }
    
    setupWorkflowSelect(workflows) {
        const select = document.getElementById('workflow-select-modal');
        if (select) {
            // Get saved workflow from localStorage
            const savedWorkflow = localStorage.getItem('nifi_chat_workflow') || 'unguided';
            console.log('ModalManager: Loading workflows, saved workflow from localStorage:', savedWorkflow);
            
            select.innerHTML = '';
            workflows.forEach(workflow => {
                const option = document.createElement('option');
                option.value = workflow.name;
                option.textContent = workflow.display_name || workflow.name;
                // Select the saved workflow, or default to 'unguided'
                if (workflow.name === savedWorkflow) {
                    option.selected = true;
                    console.log('ModalManager: Selected workflow option:', workflow.name);
                }
                select.appendChild(option);
            });
            
            // Update current workflow display to match selection
            this.updateCurrentWorkflow(savedWorkflow);
            
            // Remove any existing change listeners by cloning (this removes all event listeners)
            const oldSelect = select;
            const newSelect = oldSelect.cloneNode(true);
            oldSelect.parentNode.replaceChild(newSelect, oldSelect);
            
            // Get the fresh select element
            const freshSelect = document.getElementById('workflow-select-modal');
            if (!freshSelect) {
                console.error('ModalManager: Could not find workflow-select-modal after cloning');
                return;
            }
            
            // Add change listener to the fresh select element
            freshSelect.addEventListener('change', (e) => {
                const selectedWorkflow = e.target.value;
                console.log('ModalManager: Workflow selection changed to:', selectedWorkflow);
                console.log('ModalManager: Saving to localStorage:', selectedWorkflow);
                this.updateCurrentWorkflow(selectedWorkflow);
                this.updateWorkflowDescription(selectedWorkflow, workflows, 'workflow-description-modal');
            });
            
            console.log('ModalManager: Event listener attached to workflow select');
            
            // Set initial description
            const currentWorkflow = select.value || 'unguided';
            this.updateWorkflowDescription(currentWorkflow, workflows, 'workflow-description-modal');
        }
    }
    
    extractPhases(workflows) {
        // Extract phases from workflows first
        this.availablePhases = new Set();
        workflows.forEach(workflow => {
            if (workflow.phases) {
                workflow.phases.forEach(phase => this.availablePhases.add(phase));
            }
        });
        
        // Also extract phases from tools if available
        if (this.allTools) {
            this.allTools.forEach(tool => {
                if (tool.phases) {
                    tool.phases.forEach(phase => this.availablePhases.add(phase));
                }
            });
        }
        
        this.availablePhases = Array.from(this.availablePhases).sort();
        console.log('Final available phases:', this.availablePhases);
    }
    
    setupPhaseFilters() {
        const phaseAllCheckbox = document.getElementById('phase-all');
        const phaseCheckboxes = document.getElementById('phase-checkboxes');
        
        if (phaseAllCheckbox && phaseCheckboxes) {
            // Clear existing checkboxes
            phaseCheckboxes.innerHTML = '';
            
            // Add phase-specific checkboxes (skip "All" to avoid redundancy)
            this.availablePhases.forEach(phase => {
                if (phase.toLowerCase() !== 'all') {
                    const label = document.createElement('label');
                    label.className = 'checkbox-label';
                    label.innerHTML = `
                        <input type="checkbox" id="phase-${phase}" value="${phase}">
                        <span class="checkmark"></span>
                        ${phase}
                    `;
                    phaseCheckboxes.appendChild(label);
                    
                    // Add change listener
                    const checkbox = label.querySelector('input');
                    checkbox.addEventListener('change', () => this.handlePhaseFilterChange());
                }
            });
            
            // Add change listener for "All Phases" checkbox
            phaseAllCheckbox.addEventListener('change', () => this.handleAllPhaseChange());
        }
    }
    
    handleAllPhaseChange() {
        const phaseAllCheckbox = document.getElementById('phase-all');
        const phaseCheckboxes = document.querySelectorAll('#phase-checkboxes input[type="checkbox"]');
        
        if (phaseAllCheckbox.checked) {
            // Check all phase checkboxes
            phaseCheckboxes.forEach(checkbox => {
                checkbox.checked = true;
            });
            this.selectedPhases = new Set(['all', ...this.availablePhases]);
        } else {
            // Uncheck all phase checkboxes
            phaseCheckboxes.forEach(checkbox => {
                checkbox.checked = false;
            });
            this.selectedPhases = new Set();
        }
        
        this.filterToolsByPhase();
    }
    
    handlePhaseFilterChange() {
        const phaseAllCheckbox = document.getElementById('phase-all');
        const phaseCheckboxes = document.querySelectorAll('#phase-checkboxes input[type="checkbox"]');
        
        const checkedPhases = Array.from(phaseCheckboxes)
            .filter(checkbox => checkbox.checked)
            .map(checkbox => checkbox.value);
        
        if (checkedPhases.length === this.availablePhases.length) {
            // All phases are checked, check "All" checkbox
            phaseAllCheckbox.checked = true;
            this.selectedPhases = new Set(['all', ...this.availablePhases]);
        } else {
            // Not all phases are checked, uncheck "All" checkbox
            phaseAllCheckbox.checked = false;
            this.selectedPhases = new Set(checkedPhases);
        }
        
        this.filterToolsByPhase();
    }
    
    async loadToolsModal() {
        try {
            const response = await fetch('/api/settings/tools');
            if (response.ok) {
                const tools = await response.json();
                this.allTools = tools; // Store all tools for filtering
                
                // Debug: Log tool phases
                console.log('Tools loaded:', tools.length);
                const phasesFromTools = new Set();
                tools.forEach(tool => {
                    if (tool.phases && Array.isArray(tool.phases)) {
                        tool.phases.forEach(phase => phasesFromTools.add(phase));
                    }
                });
                console.log('Phases found in tools:', Array.from(phasesFromTools));
                
                this.filterToolsByPhase();
            }
        } catch (error) {
            console.error('Error loading tools for modal:', error);
        }
    }
    
    filterToolsByPhase() {
        if (!this.allTools) return;
        
        const toolsContainer = document.getElementById('tools-container-modal');
        if (toolsContainer) {
            let filteredTools = this.allTools;
            
            // Filter by selected phases
            if (!this.selectedPhases.has('all') && this.selectedPhases.size > 0) {
                filteredTools = this.allTools.filter(tool => {
                    // Check if tool has phase information
                    const toolPhases = tool.phases || ['all'];
                    return toolPhases.some(phase => 
                        this.selectedPhases.has(phase) || phase === 'all'
                    );
                });
            }
            
            this.renderToolsList(filteredTools, toolsContainer);
            this.updateToolCount(filteredTools.length);
        }
    }
    
    updateCurrentWorkflow(workflowName) {
        console.log('ModalManager.updateCurrentWorkflow called with:', workflowName);
        const currentWorkflowSpan = document.getElementById('current-workflow');
        if (currentWorkflowSpan) {
            currentWorkflowSpan.textContent = workflowName;
            console.log('ModalManager: Updated current-workflow span to:', workflowName);
        } else {
            console.warn('ModalManager: current-workflow span not found');
        }
        localStorage.setItem('nifi_chat_workflow', workflowName);
        console.log('ModalManager: Saved to localStorage:', workflowName, 'Current value:', localStorage.getItem('nifi_chat_workflow'));
    }
    
    updateWorkflowDescription(workflowName, workflows, descriptionElementId) {
        const descriptionElement = document.getElementById(descriptionElementId);
        if (descriptionElement) {
            const selectedWorkflow = workflows.find(w => w.name === workflowName);
            if (selectedWorkflow) {
                descriptionElement.innerHTML = `
                    <strong>Description:</strong> ${selectedWorkflow.description}<br>
                    <strong>Phases:</strong> ${selectedWorkflow.phases?.join(', ') || 'All'}
                `;
            } else {
                descriptionElement.innerHTML = '';
            }
        }
    }
    
    renderToolsList(tools, container) {
        container.innerHTML = '';
        if (tools && tools.length > 0) {
            tools.forEach(tool => {
                const toolDiv = document.createElement('div');
                toolDiv.className = 'tool-item';
                
                const requiredParams = tool.function?.parameters?.required || [];
                const properties = tool.function?.parameters?.properties || {};
                
                toolDiv.innerHTML = `
                    <div class="tool-header" onclick="this.parentElement.querySelector('.tool-content').classList.toggle('show')">
                        <span class="tool-name">🔧 ${tool.function?.name || 'Unknown Tool'}</span>
                        <button class="tool-toggle">▼</button>
                    </div>
                    <div class="tool-content">
                        <div class="tool-description">${this.formatToolDescription(tool.function?.description || 'No description')}</div>
                        <div class="tool-parameters">
                            <strong>Parameters:</strong>
                            ${Object.entries(properties).map(([name, prop]) => `
                                <div class="tool-parameter">
                                    <span class="${requiredParams.includes(name) ? 'parameter-required' : ''}">${requiredParams.includes(name) ? '✳️' : '○'} ${name}</span>: ${prop.description || 'No description'}
                                </div>
                            `).join('')}
                        </div>
                    </div>
                `;
                
                container.appendChild(toolDiv);
            });
        } else {
            container.innerHTML = '<div class="loading">No tools available for selected phases</div>';
        }
    }
    
    formatToolDescription(description) {
        // Convert **text** to proper headers
        description = description.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
        
        // Convert ```code``` to proper code blocks
        description = description.replace(/```([\s\S]*?)```/g, '<pre><code>$1</code></pre>');
        
        // Convert `inline code` to proper inline code
        description = description.replace(/`([^`]+)`/g, '<code>$1</code>');
        
        return description;
    }
    updateToolCount(count) {
        // Update the section header tool count
        const toolCountSpan = document.querySelector('.modal-section h4 .tool-count');
        if (toolCountSpan) {
            toolCountSpan.textContent = `(${count})`;
        }
    }
}

export default ModalManager;
