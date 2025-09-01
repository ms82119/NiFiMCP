/**
 * NiFi Chat UI - JavaScript Application
 * 
 * This module handles the frontend logic including WebSocket communication,
 * real-time UI updates, and chat functionality.
 */

import ThemeManager from './js/theme-manager.js';
import ModalManager from './js/modal-manager.js';
import MarkdownRenderer from './js/markdown-renderer.js';

class NiFiChatApp {
    constructor() {
        this.ws = null;
        this.currentWorkflowId = null;
        this.isConnected = false;
        this.reconnectAttempts = 0;
        this.maxReconnectAttempts = 5;
        this.reconnectDelay = 1000;
        
        // Initialize managers
        this.themeManager = new ThemeManager();
        this.modalManager = new ModalManager();
        this.markdownRenderer = new MarkdownRenderer();
        
        // Initialize the application
        this.init();
    }
    
    init() {
        console.log('Initializing NiFi Chat App...');
        this.setupEventListeners();
        this.connectWebSocket();
        this.loadChatHistory();
        this.loadSettings();
        this.updateConnectionStatus('connecting');
        console.log('NiFi Chat App initialization complete');
    }
    
    setupEventListeners() {
        console.log('Setting up event listeners...');
        
        // Send button
        const sendButton = document.getElementById('send-button');
        if (sendButton) {
            sendButton.addEventListener('click', () => this.sendMessage());
        }
        
        // User input
        const userInput = document.getElementById('user-input');
        if (userInput) {
            userInput.addEventListener('keydown', (e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault();
                    this.sendMessage();
                }
            });
            
            // Auto-resize textarea
            userInput.addEventListener('input', () => {
                this.autoResizeTextarea(userInput);
                this.updateCharCount();
            });
        }

        // Objective input
        const objectiveInput = document.getElementById('objective-input');
        if (objectiveInput) {
            objectiveInput.addEventListener('input', () => {
                this.autoResizeTextarea(objectiveInput);
            });
        }
        
        // Stop workflow button
        const stopButton = document.getElementById('stop-workflow-btn');
        if (stopButton) {
            stopButton.addEventListener('click', () => this.stopWorkflow());
        }
        
        // Settings panel
        const settingsToggleBtn = document.getElementById('settings-toggle-btn');
        const settingsPanel = document.getElementById('settings-panel');
        const settingsCloseBtn = document.getElementById('settings-close-btn');
        
        if (settingsToggleBtn && settingsPanel) {
            console.log('Settings toggle button found, adding click listener');
            settingsToggleBtn.addEventListener('click', (e) => {
                e.preventDefault();
                e.stopPropagation();
                console.log('Settings button clicked');
                
                // Show panel immediately
                settingsPanel.style.display = 'block';
                settingsPanel.style.visibility = 'visible';
                
                // Force a reflow for Safari
                settingsPanel.offsetHeight;
                
                // Add show class after a brief delay
                setTimeout(() => {
                    settingsPanel.classList.add('show');
                }, 50);
            });
        } else {
            console.error('Settings elements not found:', { settingsToggleBtn, settingsPanel });
        }
        
        if (settingsCloseBtn && settingsPanel) {
            settingsCloseBtn.addEventListener('click', () => {
                this.closeSettingsPanel();
            });
        }
        
        // Close settings panel when clicking outside
        document.addEventListener('click', (e) => {
            if (settingsPanel && settingsPanel.classList.contains('show')) {
                if (!settingsPanel.contains(e.target) && !settingsToggleBtn.contains(e.target)) {
                    this.closeSettingsPanel();
                }
            }
        });
        
        // Settings change events
        const modelSelect = document.getElementById('model-select');
        if (modelSelect) {
            modelSelect.addEventListener('change', (e) => {
                this.updateSettings('model', e.target.value);
            });
        }
        
        const serverSelect = document.getElementById('nifi-server-select');
        if (serverSelect) {
            serverSelect.addEventListener('change', (e) => {
                this.updateSettings('nifi_server', e.target.value);
            });
        }
        
        const workflowSelect = document.getElementById('workflow-select');
        if (workflowSelect) {
            workflowSelect.addEventListener('change', (e) => {
                this.updateSettings('workflow', e.target.value);
            });
        }
        
        // Clear chat button (in settings)
        const clearButton = document.getElementById('clear-chat-btn');
        if (clearButton) {
            clearButton.addEventListener('click', () => this.clearChat());
        }

        // Clear chat button (in footer)
        const clearButtonFooter = document.getElementById('clear-chat-btn-footer');
        if (clearButtonFooter) {
            clearButtonFooter.addEventListener('click', () => this.clearChat());
        }
        
        // Copy conversation button
        const copyButton = document.getElementById('copy-conversation-btn');
        if (copyButton) {
            copyButton.addEventListener('click', () => this.copyConversation());
        }
        
        // Workflow modal button
        const workflowModalBtn = document.getElementById('workflow-modal-btn');
        if (workflowModalBtn) {
            workflowModalBtn.addEventListener('click', () => this.modalManager.openWorkflowModal());
        }
        
        // Copy conversation button in footer
        const copyConversationBtnFooter = document.getElementById('copy-conversation-btn-footer');
        if (copyConversationBtnFooter) {
            copyConversationBtnFooter.addEventListener('click', () => this.copyConversation());
        }
        
        console.log('Event listeners setup complete');
    }
    
    connectWebSocket() {
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${protocol}//${window.location.host}/ws`;
        
        this.ws = new WebSocket(wsUrl);
        
        this.ws.onopen = () => {
            console.log('WebSocket connected');
            this.isConnected = true;
            this.reconnectAttempts = 0;
            this.updateConnectionStatus('connected');
        };
        
        this.ws.onmessage = (event) => {
            this.handleWebSocketMessage(event);
        };
        
        this.ws.onclose = () => {
            console.log('WebSocket disconnected');
            this.isConnected = false;
            this.updateConnectionStatus('disconnected');
            this.attemptReconnect();
        };
        
        this.ws.onerror = (error) => {
            console.error('WebSocket error:', error);
            this.updateConnectionStatus('disconnected');
        };
    }
    
    attemptReconnect() {
        if (this.reconnectAttempts < this.maxReconnectAttempts) {
            this.reconnectAttempts++;
            this.updateConnectionStatus('connecting');
            
            setTimeout(() => {
                console.log(`Attempting to reconnect (${this.reconnectAttempts}/${this.maxReconnectAttempts})`);
                this.connectWebSocket();
            }, this.reconnectDelay * this.reconnectAttempts);
        } else {
            this.showError('Failed to connect to server. Please refresh the page.');
        }
    }
    
    handleWebSocketMessage(event) {
        try {
            const data = JSON.parse(event.data);
            console.log('Received WebSocket message:', data);
            
            switch (data.type) {
                case 'workflow_start':
                    this.handleWorkflowStart(data);
                    break;
                    
                case 'workflow_complete':
                    this.handleWorkflowComplete(data);
                    break;
                    
                case 'event':
                    this.handleWorkflowEvent(data);
                    break;
                    
                case 'workflow_stopped':
                    this.handleWorkflowStopped(data);
                    break;
                    
                case 'workflow_status':
                    this.handleWorkflowStatus(data);
                    break;
                    
                case 'pong':
                    // Handle ping/pong for connection health
                    break;
                    
                case 'error':
                    this.showError(data.message || 'An error occurred');
                    break;
                    
                default:
                    console.warn('Unknown message type:', data.type);
            }
        } catch (error) {
            console.error('Error parsing WebSocket message:', error);
        }
    }
    
    async sendMessage() {
        const input = document.getElementById('user-input');
        const objectiveInput = document.getElementById('objective-input');
        const message = input.value.trim();
        const objective = objectiveInput.value.trim();
        
        if (!message) return;
        
        if (!this.isConnected) {
            this.showError('Not connected to server. Please wait for connection.');
            return;
        }
        
        // Add user message to UI
        this.addUserMessage(message);
        
        // Clear input
        input.value = '';
        this.autoResizeTextarea(input);
        this.updateCharCount();
        
        // Don't show loading modal - we'll use live response bubble instead
        // this.showLoading(true);
        
        try {
            // Get selected settings
            const modelSelect = document.getElementById('model-select');
            const serverSelect = document.getElementById('nifi-server-select');
            const workflowSelect = document.getElementById('workflow-select');
            
            // Get smart purging settings
            const autoPruneHistory = document.getElementById('auto-prune-history').checked;
            const maxTokensLimit = parseInt(document.getElementById('max-tokens-limit').value);
            const maxActions = parseInt(document.getElementById('max-actions').value);
            
            let provider = 'openai';
            let model_name = 'gpt-4o-mini';
            
            if (modelSelect.value) {
                const [selectedProvider, selectedModel] = modelSelect.value.split(':');
                provider = selectedProvider;
                model_name = selectedModel;
            }
            
            // Build system prompt with objective if provided
            let systemPrompt = "You are a helpful NiFi assistant. Help the user with their request.";
            if (objective) {
                systemPrompt += `\n\nObjective: ${objective}`;
            }
            
            // Send to backend
            const response = await fetch('/api/chat/', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    content: message,
                    objective: objective,
                    provider: provider,
                    model_name: model_name,
                    selected_nifi_server_id: serverSelect.value || null,
                    // Include smart purging settings
                    auto_prune_history: autoPruneHistory,
                    max_tokens_limit: maxTokensLimit,
                    max_loop_iterations: maxActions
                })
            });
            
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}: ${response.statusText}`);
            }
            
            const result = await response.json();
            this.currentWorkflowId = result.request_id;
            
            console.log('Chat message submitted:', result);
            
        } catch (error) {
            console.error('Error sending message:', error);
            this.showError(`Failed to send message: ${error.message}`);
            // this.showLoading(false);
        }
    }
    
    stopWorkflow() {
        if (this.currentWorkflowId && this.isConnected) {
            this.ws.send(JSON.stringify({
                type: 'stop_workflow',
                request_id: this.currentWorkflowId
            }));
            
            console.log('Stop workflow request sent for:', this.currentWorkflowId);
        }
    }
    
    handleWorkflowStart(data) {
        console.log('Workflow started:', data);
        
        this.showStopButton();
        this.hideInputForm();
        // Don't add a separate system message - this will be handled in the live response bubble
    }
    
    handleWorkflowComplete(data) {
        console.log('Workflow completed:', data);
        
        const requestId = data.request_id;
        
        // Stop the live timer
        this.stopLiveTimer(requestId);
        
        this.hideStopButton();
        this.showInputForm();
        // this.showLoading(false);
        
        // Remove the live response bubble
        const liveContainer = document.getElementById(`live-response-${requestId}`);
        if (liveContainer) {
            liveContainer.remove();
        }
        
        if (data.result && data.result.content) {
                    // Add the final content to aggregated content
        if (!this.aggregatedContent) {
            this.aggregatedContent = {};
        }
        if (!this.aggregatedContent[requestId]) {
            this.aggregatedContent[requestId] = [];
        }
        
                        // Add the final response content
                this.aggregatedContent[requestId].push(data.result.content);
            
            // Create the final aggregated message
            this.createFinalAggregatedMessage(requestId, data.result);
            
        } else if (data.result && data.result.error) {
            this.addSystemMessage(`❌ Workflow failed: ${data.result.error}`);
        } else {
            this.addSystemMessage('✅ Workflow completed successfully');
        }
        
        this.currentWorkflowId = null;
    }
    
    createFinalAggregatedMessage(requestId, result) {
        // Create the final aggregated response
        const messageDiv = document.createElement('div');
        messageDiv.className = 'message assistant-message final-response';
        
        const contentDiv = document.createElement('div');
        contentDiv.className = 'message-content';
        
        let html = '';
        
        // Add execution steps (tool calls and LLM responses) - preserve all details
        if (this.aggregatedSteps && this.aggregatedSteps[requestId]) {
            html += '<div class="execution-steps">';
            this.aggregatedSteps[requestId].forEach(step => {
                html += `<div class="status-update">${this.formatStatusMessage(step)}</div>`;
            });
            html += '</div>';
        }
        
        // Add aggregated content (LLM responses)
        if (this.aggregatedContent && this.aggregatedContent[requestId]) {
            if (html) html += '<hr>';
            html += '<div class="aggregated-content">';
            this.aggregatedContent[requestId].forEach(content => {
                html += `<div class="content-part">${this.formatMessage(content)}</div>`;
            });
            html += '</div>';
        }
        
        // Add final execution summary
        if (html) html += '<hr>';
        html += '<div class="execution-summary">';
        html += '<div class="summary-stats">';
        
        const stats = [];
        
        // Add model name if available
        if (this.aggregatedStatus && this.aggregatedStatus[requestId] && this.aggregatedStatus[requestId].model_name) {
            const modelName = this.aggregatedStatus[requestId].model_name;
            if (modelName !== 'unknown') {
                stats.push(`🤖 ${modelName}`);
            }
        }
        
        // Use consistent token display format (same as live status)
        if (result.metadata && result.metadata.token_count_in && result.metadata.token_count_out) {
            stats.push(`📊 ${result.metadata.token_count_in.toLocaleString()}/${result.metadata.token_count_out.toLocaleString()}`);
        }
        
        if (result.metadata && result.metadata.tool_calls_count) {
            stats.push(`🔧 ${result.metadata.tool_calls_count} tools executed`);
        }
        
        // Add elapsed time
        if (this.aggregatedStatus && this.aggregatedStatus[requestId]) {
            const status = this.aggregatedStatus[requestId];
            const elapsed = Math.floor((Date.now() - status.start_time) / 1000);
            stats.push(`⏱️ ${elapsed}s`);
        }
        
        html += stats.join(' | ');
        html += '</div>';
        html += '</div>';
        
        contentDiv.innerHTML = html;
        messageDiv.appendChild(contentDiv);
        
        // Insert into chat history
        const chatHistory = document.getElementById('chat-history');
        chatHistory.appendChild(messageDiv);
        
        // Scroll to bottom
        this.scrollToBottom();
        
        // Save the complete response to database for persistence
        this.saveCompleteResponse(requestId, html, result);
        
        // Clean up aggregated data for this request
        if (this.aggregatedContent && this.aggregatedContent[requestId]) {
            delete this.aggregatedContent[requestId];
        }
        if (this.aggregatedSteps && this.aggregatedSteps[requestId]) {
            delete this.aggregatedSteps[requestId];
        }
        if (this.aggregatedStatus && this.aggregatedStatus[requestId]) {
            delete this.aggregatedStatus[requestId];
        }
    }
    
    handleWorkflowEvent(data) {
        console.log('Workflow event:', data);
        
        // Handle different event types
        switch (data.event_type) {
            case 'llm_start':
                this.addSystemMessage('🤔 LLM processing...');
                break;
                
            case 'tool_start':
                this.addSystemMessage(`⚙️ Executing: ${data.data.tool_name || 'tool'}`);
                break;
                
            case 'tool_complete':
                this.addSystemMessage(`✅ Tool completed: ${data.data.tool_name || 'tool'}`);
                break;
                
            default:
                // For other events, we could add them to a progress indicator
                break;
        }
    }
    
    handleWorkflowStopped(data) {
        console.log('Workflow stopped:', data);
        
        this.hideStopButton();
        this.showInputForm();
        // this.showLoading(false);
        this.addSystemMessage('⏹️ Workflow stopped by user');
        this.currentWorkflowId = null;
    }
    
    handleWorkflowStatus(data) {
        console.log('Workflow status:', data);
        
        // Update or create live response bubble
        this.updateLiveResponse(data);
    }
    
    updateLiveResponse(data) {
        const requestId = data.request_id;
        
        // Get or create live response container
        let liveContainer = document.getElementById(`live-response-${requestId}`);
        if (!liveContainer) {
            liveContainer = document.createElement('div');
            liveContainer.id = `live-response-${requestId}`;
            liveContainer.className = 'message assistant-message live-response';
            
            const contentDiv = document.createElement('div');
            contentDiv.className = 'message-content live-content';
            liveContainer.appendChild(contentDiv);
            
            // Insert after the last user message
            const chatHistory = document.getElementById('chat-history');
            chatHistory.appendChild(liveContainer);
        }
        
        // Get or create aggregated content for this request
        if (!this.aggregatedContent) {
            this.aggregatedContent = {};
        }
        if (!this.aggregatedSteps) {
            this.aggregatedSteps = {};
        }
        if (!this.aggregatedStatus) {
            this.aggregatedStatus = {};
        }
        
        if (!this.aggregatedContent[requestId]) {
            this.aggregatedContent[requestId] = [];
        }
        if (!this.aggregatedSteps[requestId]) {
            this.aggregatedSteps[requestId] = [];
        }
        if (!this.aggregatedStatus[requestId]) {
            this.aggregatedStatus[requestId] = {
                workflow_name: 'unguided',
                current_status: 'Starting...',
                tokens_in: 0,
                tokens_out: 0,
                tools_available: 0,
                context_size: 0,
                start_time: Date.now()
            };
        }
        
        // Update status based on message type
        this.updateStatusInfo(requestId, data);
        
        // Add the status update to aggregated steps (but filter out transient messages)
        if (this.shouldShowAsStep(data)) {
            let stepLine = data.message;
            
            // Add single icons for different message types
            if (data.message.includes('LLM step:')) {
                stepLine = `🤖 ${data.message}`;
            } else if (data.message.includes('Executing:')) {
                stepLine = `🔧 ${data.message}`;
            } else if (data.message.includes('Tool Completed:')) {
                stepLine = `✅ ${data.message}`;
            }
            // Workflow start message gets no icon
            
            // Avoid duplicate consecutive steps
            if (!this.aggregatedSteps[requestId].length || 
                this.aggregatedSteps[requestId][this.aggregatedSteps[requestId].length - 1] !== stepLine) {
                this.aggregatedSteps[requestId].push(stepLine);
            }
        }
        
        // Update the content
        const contentDiv = liveContainer.querySelector('.live-content');
        
        // Build the aggregated display
        let html = '';
        
        // Show execution steps (tool calls and LLM responses)
        if (this.aggregatedSteps[requestId].length > 0) {
            html += '<div class="execution-steps">';
            this.aggregatedSteps[requestId].forEach(step => {
                html += `<div class="status-update">${this.formatStatusMessage(step)}</div>`;
            });
            html += '</div>';
        }
        
        // Show aggregated content (LLM responses)
        if (this.aggregatedContent[requestId].length > 0) {
            if (html) html += '<hr>';
            html += '<div class="aggregated-content">';
            this.aggregatedContent[requestId].forEach((content, index) => {
                html += `<div class="content-part">${this.formatMessage(content)}</div>`;
            });
            html += '</div>';
        }
        
        // Show live status section
        if (html) html += '<hr>';
        html += this.buildStatusSection(requestId);
        
        contentDiv.innerHTML = html;
        
        // Start live timer updates if not already started
        this.startLiveTimer(requestId);
        
        // Scroll to bottom
        this.scrollToBottom();
    }
    
    shouldShowAsStep(data) {
        // Only show tool calls and LLM responses as steps, not transient status messages
        const message = data.message || '';
        return message.includes('Executing:') || 
               message.includes('Tool Completed:') || 
               message.includes('LLM step:') ||
               (message.includes('Workflow execution started') && message.includes('unguided')); // Only show the one with workflow name
    }
    
    updateStatusInfo(requestId, data) {
        const status = this.aggregatedStatus[requestId];
        const message = data.message || '';
        
        // Apply styling to Request and Action IDs in the message
        let styledMessage = message
            .replace(/\(Req: ([^)]+)\)/g, '<span class="id-label">Req:</span><span class="request-id">$1</span>')
            .replace(/\(Act: ([^)]+)\)/g, '<span class="id-label">Act:</span><span class="action-id">$1</span>');
        
        if (data.status === 'started') {
            status.current_status = 'Workflow started';
        } else if (message.includes('LLM Call Started')) {
            status.current_status = 'LLM processing';
            // Extract model name and other info from LLM start event
            if (data.data) {
                status.model_name = data.data.model_name || data.data.model || 'unknown';
                status.provider = data.data.provider || 'unknown';
                status.messages_in_request = data.data.messages_in_request || 0;
                status.tools_available = data.data.tools_available || 0;
            }
        } else if (message.includes('Executing:')) {
            status.current_status = 'Tool execution';
        } else if (message.includes('LLM step:')) {
            // Extract token counts from LLM step message - handle comma-separated numbers
            const tokenMatch = message.match(/([\d,]+) in, ([\d,]+) out/);
            if (tokenMatch) {
                status.tokens_in = parseInt(tokenMatch[1].replace(/,/g, ''));
                status.tokens_out = parseInt(tokenMatch[2].replace(/,/g, ''));
            }
        }
        
        // Update the styled message
        status.current_message = styledMessage;
    }
    
    buildStatusSection(requestId) {
        const status = this.aggregatedStatus[requestId];
        const elapsed = Math.floor((Date.now() - status.start_time) / 1000);
        
        // Use animated icon for processing status
        let statusIcon = '🔄';
        if (status.current_status === 'LLM processing' || status.current_status === 'Tool execution') {
            statusIcon = '<span class="working-dots">⟳</span>';
        }
        
        const stats = [];
        
        // Status with model name
        if (status.model_name && status.model_name !== 'unknown') {
            stats.push(`${statusIcon} ${status.current_status} (${status.model_name})`);
        } else {
            stats.push(`${statusIcon} ${status.current_status}`);
        }
        
        // Token usage
        stats.push(`📊 ${status.tokens_in.toLocaleString()}/${status.tokens_out.toLocaleString()}`);
        
        // Live timer
        stats.push(`⏱️ ${elapsed}s`);
        
        // Tools available
        stats.push(`🔧 ${status.tools_available} tools`);
        
        // Messages in request (non-system messages)
        stats.push(`💬 ${status.messages_in_request} msgs`);
        
        return `
            <div class="live-status-section">
                <div class="status-metrics">
                    ${stats.join(' | ')}
                </div>
            </div>
        `;
    }
    
    startLiveTimer(requestId) {
        // Stop any existing timer for this request
        if (this.liveTimers && this.liveTimers[requestId]) {
            clearInterval(this.liveTimers[requestId]);
        }
        
        // Initialize live timers object if it doesn't exist
        if (!this.liveTimers) {
            this.liveTimers = {};
        }
        
        // Start a new timer that updates every second
        this.liveTimers[requestId] = setInterval(() => {
            const liveContainer = document.getElementById(`live-response-${requestId}`);
            if (liveContainer) {
                // Update the status section with current time
                const contentDiv = liveContainer.querySelector('.live-content');
                if (contentDiv) {
                    // Rebuild the status section with updated time
                    const statusSection = this.buildStatusSection(requestId);
                    const currentHtml = contentDiv.innerHTML;
                    
                    // Replace the status section part
                    const updatedHtml = currentHtml.replace(
                        /<div class="live-status-section">[\s\S]*?<\/div>\s*<\/div>\s*$/,
                        statusSection
                    );
                    
                    contentDiv.innerHTML = updatedHtml;
                }
            } else {
                // Container no longer exists, stop the timer
                clearInterval(this.liveTimers[requestId]);
                delete this.liveTimers[requestId];
            }
        }, 1000);
    }
    
    stopLiveTimer(requestId) {
        if (this.liveTimers && this.liveTimers[requestId]) {
            clearInterval(this.liveTimers[requestId]);
            delete this.liveTimers[requestId];
        }
    }
    
    async saveCompleteResponse(requestId, html, result) {
        try {
            // Stop the live timer when workflow completes
            this.stopLiveTimer(requestId);
            
            // Save the complete response to the database
            const response = await fetch('/api/chat/save-complete-response', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    request_id: requestId,
                    content: html,
                    metadata: result.metadata || {}
                })
            });
            
            if (!response.ok) {
                console.error('Failed to save complete response to database');
            } else {
                console.log('Successfully saved complete response to database');
            }
        } catch (error) {
            console.error('Error saving complete response:', error);
        }
    }
    
    // UI Helper Methods
    
    addUserMessage(content) {
        this.addMessage('user', content);
    }
    
    addAssistantMessage(content, metadata = null) {
        this.addMessage('assistant', content, metadata);
    }
    
    addSystemMessage(content) {
        this.addMessage('system', content);
    }
    
    addMessage(role, content, metadata = null) {
        const chatHistory = document.getElementById('chat-history');
        const messageDiv = document.createElement('div');
        messageDiv.className = `message ${role}-message`;
        
        const contentDiv = document.createElement('div');
        contentDiv.className = 'message-content';
        
        // Check if content already contains HTML (from saved database content)
        // Look for common HTML tags that indicate this is pre-formatted content
        const hasHtml = content.includes('<') && content.includes('>') && 
                       (content.includes('<div') || content.includes('<span') || 
                        content.includes('<br') || content.includes('<table') ||
                        content.includes('<p>') || content.includes('<h'));
        
        // Check if this is an assistant message with tool results
        if (role === 'assistant' && metadata && metadata.has_tool_results) {
            contentDiv.innerHTML = this.formatToolResults(content, metadata);
        } else if (hasHtml) {
            // Content already contains HTML, clean it up and use it directly without re-formatting
            const cleanedContent = this.cleanupHtmlContent(content);
            contentDiv.innerHTML = cleanedContent;
            
            // Re-render Mermaid diagrams in the loaded content
            if (this.markdownRenderer && cleanedContent.includes('mermaid-container')) {
                setTimeout(() => {
                    this.markdownRenderer.autoRenderMermaidDiagrams();
                }, 100);
            }
        } else {
            // Format content (basic markdown-like formatting)
            contentDiv.innerHTML = this.formatMessage(content);
        }
        
        messageDiv.appendChild(contentDiv);
        chatHistory.appendChild(messageDiv);
        
        // Scroll to bottom
        this.scrollToBottom();
    }
    
    formatMessage(content) {
        // Check if content contains markdown patterns
        if (this.markdownRenderer.hasMarkdown(content)) {
            // Render as markdown
            return `<div class="markdown-content">${this.markdownRenderer.render(content)}</div>`;
        } else {
            // Simple text formatting for plain content
            return content
                .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
                .replace(/\*(.*?)\*/g, '<em>$1</em>')
                .replace(/`(.*?)`/g, '<code>$1</code>')
                .replace(/\n/g, '<br>');
        }
    }
    
    formatStatusMessage(content) {
        // Special formatting for status messages (tool execution, etc.)
        // Don't use markdown renderer for these, just basic formatting
        
        // Apply styling to Request and Action IDs in the message
        let formattedContent = content
            .replace(/\(Req: ([^)]+)\)/g, '<span class="request-id">Req: $1</span>')
            .replace(/\(Act: ([^)]+)\)/g, '<span class="action-id">Act: $1</span>')
            .replace(/`([^`]+)`/g, '<code>$1</code>');
        
        // Only add br tags if content doesn't already contain HTML
        if (!formattedContent.includes('<') || !formattedContent.includes('>')) {
            formattedContent = formattedContent.replace(/\n/g, '<br>');
        }
        
        return formattedContent;
    }
    
    cleanupHtmlContent(content) {
        // Clean up excessive <br> tags and whitespace in saved HTML content
        let cleaned = content;
        
        // Clean up excessive <br> tags (more than 2 consecutive)
        cleaned = cleaned.replace(/(<br\s*\/?>\s*){3,}/gi, '<br><br>');
        
        // Clean up <br> tags inside table wrappers
        cleaned = cleaned.replace(/(<div class="markdown-table-wrapper">)\s*(<br\s*\/?>\s*)+/gi, '$1');
        cleaned = cleaned.replace(/(<br\s*\/?>\s*)+(<\/div>)/gi, '$2');
        
        // Clean up excessive whitespace around tables
        cleaned = cleaned.replace(/(<div class="markdown-table-wrapper">)\s*(<table[^>]*>)/gi, '$1$2');
        cleaned = cleaned.replace(/(<\/table>)\s*(<\/div>)/gi, '$1$2');
        
        // Clean up <br> tags that are immediately followed by closing tags
        cleaned = cleaned.replace(/<br\s*\/?>\s*(<\/[^>]+>)/gi, '$1');
        
        // Clean up <br> tags that are immediately preceded by opening tags
        cleaned = cleaned.replace(/(<[^>]+>)\s*<br\s*\/?>/gi, '$1');
        
        return cleaned;
    }
    
    formatToolResults(content, metadata) {
        let html = this.formatMessage(content);
        
        // Add tool execution details if available
        if (metadata.tool_results && metadata.tool_results.length > 0) {
            html += '<div class="tool-execution-details">';
            html += '<h4>🔧 Tool Execution Details:</h4>';
            
            metadata.tool_results.forEach((result, index) => {
                html += '<div class="tool-result">';
                html += `<h5>${result.tool_name}</h5>`;
                
                if (result.error) {
                    html += `<div class="tool-error">❌ Error: ${result.error}</div>`;
                } else {
                    html += '<div class="tool-success">✅ Success</div>';
                    
                    // Format tool result data
                    if (result.result) {
                        html += '<div class="tool-data">';
                        if (typeof result.result === 'object') {
                            html += '<pre><code>' + JSON.stringify(result.result, null, 2) + '</code></pre>';
                        } else {
                            html += '<pre><code>' + result.result + '</code></pre>';
                        }
                        html += '</div>';
                    }
                }
                html += '</div>';
            });
            
            html += '</div>';
        }
        
        // Add token usage info if available
        if (metadata.token_count_in || metadata.token_count_out) {
            html += '<div class="token-usage">';
            html += '<small>💡 Tokens: ' + (metadata.token_count_in || 0) + ' in, ' + (metadata.token_count_out || 0) + ' out</small>';
            html += '</div>';
        }
        
        return html;
    }
    
    showStopButton() {
        document.getElementById('stop-button').style.display = 'block';
    }
    
    hideStopButton() {
        document.getElementById('stop-button').style.display = 'none';
    }
    
    showInputForm() {
        document.getElementById('input-form').style.display = 'block';
    }
    
    hideInputForm() {
        document.getElementById('input-form').style.display = 'none';
    }
    
    showLoading(show) {
        const overlay = document.getElementById('loading-overlay');
        overlay.style.display = show ? 'flex' : 'none';
    }
    
    updateConnectionStatus(status) {
        const indicator = document.getElementById('connection-indicator');
        const text = document.getElementById('connection-text');
        
        indicator.className = `connection-indicator ${status}`;
        
        switch (status) {
            case 'connected':
                text.textContent = 'Connected';
                break;
            case 'connecting':
                text.textContent = 'Connecting...';
                break;
            case 'disconnected':
                text.textContent = 'Disconnected';
                break;
        }
    }
    
    autoResizeTextarea(textarea) {
        textarea.style.height = 'auto';
        textarea.style.height = Math.min(textarea.scrollHeight, 120) + 'px';
    }
    
    updateCharCount() {
        const input = document.getElementById('user-input');
        const count = input.value.length;
        document.getElementById('char-count').textContent = count;
        
        // Change color if approaching limit
        const charCountElement = document.querySelector('.char-count');
        if (count > 1800) {
            charCountElement.style.color = '#ef4444';
        } else if (count > 1500) {
            charCountElement.style.color = '#f59e0b';
        } else {
            charCountElement.style.color = '#64748b';
        }
    }
    
    scrollToBottom() {
        const chatContainer = document.querySelector('.chat-container');
        chatContainer.scrollTop = chatContainer.scrollHeight;
    }
    
    async loadChatHistory() {
        try {
            const response = await fetch('/api/chat/history?limit=20');
            if (response.ok) {
                const data = await response.json();
                
                // Clear existing messages (except welcome message)
                const chatHistory = document.getElementById('chat-history');
                const welcomeMessage = chatHistory.querySelector('.system-message');
                chatHistory.innerHTML = '';
                if (welcomeMessage) {
                    chatHistory.appendChild(welcomeMessage);
                }
                
                // Add historical messages
                data.messages.forEach(msg => {
                    if (msg.role === 'user') {
                        this.addUserMessage(msg.content);
                    } else if (msg.role === 'assistant') {
                        this.addAssistantMessage(msg.content, msg.metadata);
                    }
                });
                
                this.scrollToBottom();
                
                // Re-render all Mermaid diagrams after loading chat history
                if (this.markdownRenderer) {
                    setTimeout(() => {
                        this.markdownRenderer.autoRenderMermaidDiagrams();
                    }, 200);
                }
            }
        } catch (error) {
            console.error('Error loading chat history:', error);
        }
    }
    
    async clearChat() {
        if (confirm('Are you sure you want to clear the chat history? This will permanently delete all conversation history.')) {
            try {
                // Call API to clear chat history from database
                const response = await fetch('/api/chat/history', {
                    method: 'DELETE'
                });
                
                if (response.ok) {
                    // Clear the UI
                    const chatHistory = document.getElementById('chat-history');
                    chatHistory.innerHTML = '';
                    
                    // Add welcome message back
                    const welcomeMessage = document.createElement('div');
                    welcomeMessage.className = 'message system-message';
                    welcomeMessage.innerHTML = `
                        <div class="message-content">
                            <h3>Welcome to NiFi Chat UI! 🚀</h3>
                            <p>I'm here to help you manage your NiFi workflows. You can ask me to:</p>
                            <ul>
                                <li>Create and configure NiFi processors</li>
                                <li>Manage process groups and connections</li>
                                <li>Monitor flow status and performance</li>
                                <li>Debug and troubleshoot issues</li>
                                <li>And much more!</li>
                            </ul>
                            <p>Just type your request below and I'll help you get started.</p>
                        </div>
                    `;
                    chatHistory.appendChild(welcomeMessage);
                    
                    console.log('Chat history cleared successfully');
                } else {
                    console.error('Failed to clear chat history:', response.status);
                    this.showError('Failed to clear chat history from database');
                }
            } catch (error) {
                console.error('Error clearing chat history:', error);
                this.showError('Failed to clear chat history');
            }
        }
    }
    
    showError(message) {
        const modal = document.getElementById('error-modal');
        const errorMessage = document.getElementById('error-message');
        
        errorMessage.textContent = message;
        modal.style.display = 'flex';
    }
    
    clearError() {
        const modal = document.getElementById('error-modal');
        if (modal) {
            modal.style.display = 'none';
        }
    }
    
    // Settings Management
    
    async loadSettings() {
        try {
            // Load models
            await this.loadModels();
            
            // Load NiFi servers
            await this.loadNifiServers();
            
            // Load other settings
            const savedObjective = localStorage.getItem('nifi_chat_objective') || '';
            const objectiveInput = document.getElementById('objective-input');
            if (objectiveInput) {
                objectiveInput.value = savedObjective;
            }
            
            // Initialize current workflow display
            const savedWorkflow = localStorage.getItem('nifi_chat_workflow') || 'unguided';
            this.modalManager.updateCurrentWorkflow(savedWorkflow);
            
        } catch (error) {
            console.error('Error loading settings:', error);
            this.showError('Failed to load settings');
        }
    }
    
    async loadModels() {
        try {
            const response = await fetch('/api/settings/models');
            if (response.ok) {
                const models = await response.json();
                const modelSelect = document.getElementById('model-select');
                
                modelSelect.innerHTML = '';
                models.forEach(model => {
                    const option = document.createElement('option');
                    option.value = `${model.provider}:${model.name}`;
                    option.textContent = `${model.provider}: ${model.name}`;
                    modelSelect.appendChild(option);
                });
                
                // Set default
                if (models.length > 0) {
                    modelSelect.value = `${models[0].provider}:${models[0].name}`;
                }
            }
        } catch (error) {
            console.error('Error loading models:', error);
        }
    }
    
    async loadNifiServers() {
        try {
            const response = await fetch('/api/settings/nifi-servers');
            if (response.ok) {
                const servers = await response.json();
                const serverSelect = document.getElementById('nifi-server-select');
                
                serverSelect.innerHTML = '';
                if (servers && servers.length > 0) {
                    servers.forEach(server => {
                        const option = document.createElement('option');
                        option.value = server.id;
                        option.textContent = server.name;
                        serverSelect.appendChild(option);
                    });
                    
                    // Set default
                    serverSelect.value = servers[0].id;
                } else {
                    serverSelect.innerHTML = '<option value="">No servers available</option>';
                }
            } else {
                console.error('Failed to load NiFi servers:', response.status);
                const serverSelect = document.getElementById('nifi-server-select');
                serverSelect.innerHTML = '<option value="">Error loading servers</option>';
            }
        } catch (error) {
            console.error('Error loading NiFi servers:', error);
            const serverSelect = document.getElementById('nifi-server-select');
            serverSelect.innerHTML = '<option value="">Error loading servers</option>';
        }
    }
    

    
    updateSettings(type, value) {
        console.log(`Setting ${type} to: ${value}`);
        // Store in localStorage for persistence
        localStorage.setItem(`nifi_chat_${type}`, value);
        
        // Update UI or trigger other actions as needed
        switch (type) {
            case 'model':
                // Could trigger model change in backend
                break;
            case 'nifi_server':
                // Could update server context
                break;
            case 'workflow':
                // Could update workflow context
                break;
        }
    }
    
    closeSettingsPanel() {
        const settingsPanel = document.getElementById('settings-panel');
        if (settingsPanel) {
            settingsPanel.classList.remove('show');
            setTimeout(() => {
                settingsPanel.style.display = 'none';
                settingsPanel.style.visibility = 'hidden';
            }, 300);
        }
    }
    
    copyConversation() {
        const messages = document.querySelectorAll('.message');
        let conversation = 'NiFi Chat UI Conversation\n\n';
        
        messages.forEach(message => {
            const role = message.classList.contains('user-message') ? 'User' : 
                        message.classList.contains('assistant-message') ? 'Assistant' : 'System';
            const content = message.querySelector('.message-content').textContent;
            conversation += `${role}: ${content}\n\n`;
        });
        
        // Copy to clipboard
        navigator.clipboard.writeText(conversation).then(() => {
            alert('Conversation copied to clipboard!');
        }).catch(() => {
            this.showError('Failed to copy conversation');
        });
    }

}

// Global functions for modal
function closeErrorModal() {
    if (window.app) {
        window.app.clearError();
    } else {
        document.getElementById('error-modal').style.display = 'none';
    }
}



// Initialize the application when DOM is loaded
document.addEventListener('DOMContentLoaded', () => {
    window.app = new NiFiChatApp();
});

// Handle page visibility changes for connection management
document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible' && window.app && !window.app.isConnected) {
        window.app.connectWebSocket();
    }
});
