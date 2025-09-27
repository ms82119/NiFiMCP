/**
 * Markdown Renderer - Handles rendering of Markdown content in chat responses
 */

class MarkdownRenderer {
    constructor() {
        this.init();
    }
    
    init() {
        // Load marked.js if not already loaded
        if (typeof marked === 'undefined') {
            this.loadMarkedJS();
        }
    }
    
    loadMarkedJS() {
        const script = document.createElement('script');
        script.src = 'https://cdn.jsdelivr.net/npm/marked@4.3.0/marked.min.js';
        script.onload = () => {
            this.configureMarked();
        };
        document.head.appendChild(script);
        
        // Also load Mermaid.js for diagram rendering
        this.loadMermaidJS();
    }
    
    loadMermaidJS() {
        const script = document.createElement('script');
        script.src = 'https://cdn.jsdelivr.net/npm/mermaid@10.6.1/dist/mermaid.min.js';
        script.onload = () => {
            this.configureMermaid();
        };
        document.head.appendChild(script);
    }
    
    configureMermaid() {
        if (typeof mermaid !== 'undefined') {
            // Check if we're in dark mode
            const isDarkMode = document.documentElement.getAttribute('data-theme') === 'dark';
            
            mermaid.initialize({
                startOnLoad: false,
                theme: isDarkMode ? 'dark' : 'default',
                flowchart: {
                    useMaxWidth: true,
                    htmlLabels: true
                },
                themeVariables: isDarkMode ? {
                    primaryColor: '#60a5fa',
                    primaryTextColor: '#1f2937',
                    primaryBorderColor: '#374151',
                    lineColor: '#60a5fa',
                    secondaryColor: '#ffffff',
                    tertiaryColor: '#f3f4f6'
                } : undefined
            });
        }
    }
    
    configureMarked() {
        if (typeof marked !== 'undefined') {
            // Configure marked.js options
            marked.setOptions({
                breaks: true,
                gfm: true,
                headerIds: false,
                mangle: false
            });
            
            // Custom renderer for better table styling
            const renderer = new marked.Renderer();
            
            // Custom table rendering
            renderer.table = (header, body) => {
                return `<div class="markdown-table-wrapper">
                    <table class="markdown-table">
                        <thead>${header}</thead>
                        <tbody>${body}</tbody>
                    </table>
                </div>`;
            };
            
            // Custom code block rendering
            renderer.code = (code, language) => {
                const lang = language || 'text';
                
                // Special handling for Mermaid diagrams
                if (lang === 'mermaid') {
                    const diagramId = 'mermaid-' + Math.random().toString(36).substr(2, 9);
                    return `<div class="markdown-mermaid-block">
                        <div class="mermaid-header">
                            <span class="mermaid-title">📊 Flow Diagram</span>
                            <div class="mermaid-controls">
                                <button class="mermaid-btn" onclick="window.app.markdownRenderer.renderMermaid('${diagramId}', \`${code.replace(/`/g, '\\`')}\`)">Re-render</button>
                                <button class="mermaid-btn" onclick="window.app.markdownRenderer.exportMermaid('${diagramId}', \`${code.replace(/`/g, '\\`')}\`)">Export SVG</button>
                            </div>
                        </div>
                        <div id="${diagramId}" class="mermaid-container" data-mermaid-code="${this.escapeHtml(code)}">
                            <div class="mermaid-loading">🔄 Rendering diagram...</div>
                        </div>
                    </div>`;
                }
                
                return `<div class="markdown-code-block">
                    <div class="code-header">
                        <span class="code-language">${lang}</span>
                    </div>
                    <pre><code class="language-${lang}">${this.escapeHtml(code)}</code></pre>
                </div>`;
            };
            
            // Custom inline code rendering
            renderer.codespan = (code) => {
                return `<code class="markdown-inline-code">${this.escapeHtml(code)}</code>`;
            };
            
            marked.use({ renderer });
        }
    }
    
    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }
    
    unescapeHtml(text) {
        const div = document.createElement('div');
        div.innerHTML = text;
        return div.textContent || div.innerText || '';
    }
    
    render(content) {
        if (typeof marked === 'undefined') {
            // Fallback to plain text if marked.js isn't loaded yet
            return this.escapeHtml(content);
        }
        
        try {
            // Pre-process content to handle special cases
            content = this.preprocessContent(content);
            
            // Render markdown
            const rendered = marked.parse(content);
            
            // Post-process for additional enhancements
            const processed = this.postprocessContent(rendered);
            
            // Auto-render Mermaid diagrams
            this.autoRenderMermaidDiagrams(processed);
            
            return processed;
        } catch (error) {
            console.error('Error rendering markdown:', error);
            return this.escapeHtml(content);
        }
    }
    
    preprocessContent(content) {
        // Handle any special preprocessing if needed
        return content;
    }
    
    postprocessContent(html) {
        // Add syntax highlighting classes
        html = html.replace(/<code class="language-(\w+)">/g, '<code class="language-$1 syntax-highlight">');
        
        // Clean up excessive <br> tags (more than 2 consecutive)
        html = html.replace(/(<br\s*\/?>\s*){3,}/gi, '<br><br>');
        
        // Clean up <br> tags that are immediately followed by closing tags
        html = html.replace(/<br\s*\/?>\s*(<\/[^>]+>)/gi, '$1');
        
        // Clean up <br> tags that are immediately preceded by opening tags
        html = html.replace(/(<[^>]+>)\s*<br\s*\/?>/gi, '$1');
        
        // Clean up <br> tags inside table wrappers (common issue with saved content)
        html = html.replace(/(<div class="markdown-table-wrapper">)\s*(<br\s*\/?>\s*)+/gi, '$1');
        html = html.replace(/(<br\s*\/?>\s*)+(<\/div>)/gi, '$2');
        
        // Clean up excessive whitespace around tables
        html = html.replace(/(<div class="markdown-table-wrapper">)\s*(<table[^>]*>)/gi, '$1$2');
        html = html.replace(/(<\/table>)\s*(<\/div>)/gi, '$1$2');
        
        return html;
    }
    
    // Check if content contains markdown
    hasMarkdown(content) {
        const markdownPatterns = [
            /\|.*\|.*\|/, // Tables
            /^#{1,6}\s/, // Headers
            /\*\*.*\*\*/, // Bold
            /\*.*\*/, // Italic
            /`.*`/, // Inline code
            /```[\s\S]*?```/, // Code blocks
            /\[.*\]\(.*\)/, // Links
            /^- /, // Lists
            /^\d+\. /, // Numbered lists
            /```mermaid[\s\S]*?```/ // Mermaid diagrams
        ];
        
        return markdownPatterns.some(pattern => pattern.test(content));
    }
    
    // Auto-render Mermaid diagrams in the rendered HTML
    autoRenderMermaidDiagrams(html) {
        // Use setTimeout to ensure the DOM is updated before we try to render
        setTimeout(() => {
            const mermaidBlocks = document.querySelectorAll('.markdown-mermaid-block');
            mermaidBlocks.forEach(block => {
                const container = block.querySelector('.mermaid-container');
                if (container) {
                    const loadingDiv = container.querySelector('.mermaid-loading');
                    
                    if (loadingDiv) {
                        // Get the Mermaid code from the data attribute and unescape it
                        const escapedMermaidCode = container.getAttribute('data-mermaid-code');
                        if (escapedMermaidCode) {
                            const mermaidCode = this.unescapeHtml(escapedMermaidCode);
                            this.renderMermaid(container.id, mermaidCode);
                        }
                    }
                }
            });
        }, 100);
    }
    
    cleanMermaidCode(code) {
        // Remove HTML tags and normalize whitespace
        let cleaned = code.replace(/<br\s*\/?>/gi, '\n');  // Replace <br> with newlines
        cleaned = cleaned.replace(/<[^>]*>/g, '');  // Remove any other HTML tags
        
        // Fix HTML entities that might be in the code
        cleaned = cleaned.replace(/&lt;/g, '<');
        cleaned = cleaned.replace(/&gt;/g, '>');
        cleaned = cleaned.replace(/&amp;/g, '&');
        
        // More comprehensive approach to fix node labels
        // First, identify all node definitions and fix them systematically
        const lines = cleaned.split('\n');
        const fixedLines = lines.map(line => {
            // Skip empty lines and connection lines (arrows)
            if (!line.trim() || line.includes('-->') || line.includes('->')) {
                return line;
            }
            
            // Handle node definitions like: NODE_ID(Node Label)
            // Use a more robust approach that manually handles parentheses
            const trimmedLine = line.trim();
            const nodeMatch = trimmedLine.match(/^(\w+)\(/);
            if (nodeMatch) {
                const nodeId = nodeMatch[1];
                const indent = line.match(/^(\s*)/)[1];
                const trailing = line.match(/(\s*)$/)[1];
                
                // Find the matching closing parenthesis
                const openParenIndex = trimmedLine.indexOf('(');
                let parenCount = 0;
                let closeParenIndex = -1;
                
                for (let i = openParenIndex; i < trimmedLine.length; i++) {
                    if (trimmedLine[i] === '(') {
                        parenCount++;
                    } else if (trimmedLine[i] === ')') {
                        parenCount--;
                        if (parenCount === 0) {
                            closeParenIndex = i;
                            break;
                        }
                    }
                }
                
                if (closeParenIndex !== -1) {
                    const label = trimmedLine.substring(openParenIndex + 1, closeParenIndex);
                    
                    // Clean the node ID (remove any problematic characters)
                    const cleanNodeId = nodeId.replace(/[^a-zA-Z0-9_]/g, '_');
                    
                    // Clean the label and wrap in quotes
                    let cleanLabel = label.trim();
                    
                    // Always wrap labels in quotes for safety - this is the most reliable approach
                    cleanLabel = `"${cleanLabel}"`;
                    
                    return `${indent}${cleanNodeId}(${cleanLabel})${trailing}`;
                }
            }
            
            return line;
        });
        
        cleaned = fixedLines.join('\n');
        
        // Normalize whitespace but preserve structure
        cleaned = cleaned.replace(/\n\s*\n/g, '\n');  // Remove empty lines
        cleaned = cleaned.replace(/^\s+|\s+$/gm, '');  // Trim each line
        cleaned = cleaned.trim();
        
        return cleaned;
    }
    
    // More aggressive cleaning for problematic Mermaid code
    aggressiveCleanMermaidCode(code) {
        let cleaned = this.cleanMermaidCode(code);
        
        // If the basic cleaning didn't work, try more aggressive approaches
        const lines = cleaned.split('\n');
        const fixedLines = lines.map(line => {
            // Skip empty lines and connection lines
            if (!line.trim() || line.includes('-->') || line.includes('->')) {
                return line;
            }
            
            // For node definitions, be more aggressive about cleaning
            const nodeMatch = line.match(/^(\s*)(\w+)\(([^)]+)\)(\s*)$/);
            if (nodeMatch) {
                const [, indent, nodeId, label, trailing] = nodeMatch;
                
                // Clean node ID more aggressively
                const cleanNodeId = nodeId.replace(/[^a-zA-Z0-9]/g, '_');
                
                // Simplify the label - remove problematic characters and wrap in quotes
                let cleanLabel = label.trim()
                    .replace(/[()]/g, '')  // Remove parentheses
                    .replace(/\s+/g, ' ')   // Normalize spaces
                    .replace(/[^\w\s-]/g, '')  // Remove special characters except word chars, spaces, and hyphens
                    .trim();
                
                // Always wrap in quotes for safety
                cleanLabel = `"${cleanLabel}"`;
                
                return `${indent}${cleanNodeId}(${cleanLabel})${trailing}`;
            }
            
            return line;
        });
        
        return fixedLines.join('\n').trim();
    }
    
    // Reconfigure Mermaid for theme changes
    reconfigureMermaid() {
        if (typeof mermaid !== 'undefined') {
            this.configureMermaid();
        }
    }
    
    // Re-render all Mermaid diagrams for theme changes
    async reRenderAllMermaidDiagrams() {
        if (typeof mermaid === 'undefined') {
            console.error('Mermaid.js not loaded');
            return;
        }
        
        // Reconfigure Mermaid with new theme
        this.reconfigureMermaid();
        
        // Find all rendered Mermaid diagrams and re-render them
        const mermaidBlocks = document.querySelectorAll('.markdown-mermaid-block');
        for (const block of mermaidBlocks) {
            const container = block.querySelector('.mermaid-container');
            if (container && container.classList.contains('rendered')) {
                const mermaidCode = container.getAttribute('data-mermaid-code');
                if (mermaidCode) {
                    const unescapedCode = this.unescapeHtml(mermaidCode);
                    await this.renderMermaid(container.id, unescapedCode);
                }
            }
        }
    }
    
    // Render Mermaid diagram
    async renderMermaid(containerId, mermaidCode) {
        if (typeof mermaid === 'undefined') {
            console.error('Mermaid.js not loaded');
            return;
        }
        
        try {
            const container = document.getElementById(containerId);
            if (!container) return;
            
            // Clean the Mermaid code before rendering
            const cleanedCode = this.cleanMermaidCode(mermaidCode);
            
            // Debug logging
            console.log('Original Mermaid code:', mermaidCode);
            console.log('Cleaned Mermaid code:', cleanedCode);
            
            // Clear the container and render the diagram
            container.innerHTML = '';
            container.className = 'mermaid-container rendered';
            
            const { svg } = await mermaid.render(containerId + '-svg', cleanedCode);
            container.innerHTML = svg;
            
        } catch (error) {
            console.error('Error rendering Mermaid diagram:', error);
            console.error('Original code:', mermaidCode);
            console.error('Cleaned code:', this.cleanMermaidCode(mermaidCode));
            
            const container = document.getElementById(containerId);
            if (container) {
                // Try a more aggressive cleaning approach as fallback
                const fallbackCode = this.aggressiveCleanMermaidCode(mermaidCode);
                console.log('Trying fallback cleaning:', fallbackCode);
                
                try {
                    const { svg } = await mermaid.render(containerId + '-fallback-svg', fallbackCode);
                    container.innerHTML = svg;
                    console.log('Fallback rendering succeeded');
                } catch (fallbackError) {
                    console.error('Fallback rendering also failed:', fallbackError);
                    container.innerHTML = `<div class="mermaid-error">
                        ❌ Error rendering diagram: ${error.message}
                        <details>
                            <summary>Show original code</summary>
                            <pre>${this.escapeHtml(mermaidCode)}</pre>
                        </details>
                        <details>
                            <summary>Show cleaned code</summary>
                            <pre>${this.escapeHtml(this.cleanMermaidCode(mermaidCode))}</pre>
                        </details>
                    </div>`;
                }
            }
        }
    }
    
    // Export Mermaid diagram as SVG
    async exportMermaid(containerId, mermaidCode) {
        if (typeof mermaid === 'undefined') {
            console.error('Mermaid.js not loaded');
            return;
        }
        
        try {
            const { svg } = await mermaid.render(containerId + '-export', mermaidCode);
            
            // Create download link
            const blob = new Blob([svg], { type: 'image/svg+xml' });
            const url = URL.createObjectURL(blob);
            
            const a = document.createElement('a');
            a.href = url;
            a.download = 'mermaid-diagram.svg';
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            URL.revokeObjectURL(url);
            
        } catch (error) {
            console.error('Error exporting Mermaid diagram:', error);
            alert('Error exporting diagram: ' + error.message);
        }
    }
    
    // Debug function to test Mermaid rendering
    debugMermaidRendering() {
        console.log('=== Mermaid Debug ===');
        const mermaidBlocks = document.querySelectorAll('.markdown-mermaid-block');
        mermaidBlocks.forEach((block, index) => {
            console.log(`Block ${index + 1}:`);
            const container = block.querySelector('.mermaid-container');
            if (container) {
                const escapedCode = container.getAttribute('data-mermaid-code');
                console.log('  Escaped code:', escapedCode);
                const unescapedCode = this.unescapeHtml(escapedCode);
                console.log('  Unescaped code:', unescapedCode);
                const cleanedCode = this.cleanMermaidCode(unescapedCode);
                console.log('  Cleaned code:', cleanedCode);
                
                // Test if the cleaned code would parse
                try {
                    if (typeof mermaid !== 'undefined') {
                        console.log('  Testing Mermaid parsing...');
                        // Just test the parsing without rendering
                        mermaid.parse(cleanedCode);
                        console.log('  ✅ Mermaid code should parse successfully');
                    } else {
                        console.log('  ⚠️ Mermaid.js not loaded, cannot test parsing');
                    }
                } catch (parseError) {
                    console.log('  ❌ Mermaid parsing error:', parseError.message);
                    
                    // Try aggressive cleaning as fallback
                    const aggressiveCleaned = this.aggressiveCleanMermaidCode(unescapedCode);
                    console.log('  Trying aggressive cleaning:', aggressiveCleaned);
                    try {
                        mermaid.parse(aggressiveCleaned);
                        console.log('  ✅ Aggressive cleaning should work');
                    } catch (aggressiveError) {
                        console.log('  ❌ Even aggressive cleaning failed:', aggressiveError.message);
                    }
                }
            }
        });
    }
    
    // Test function for specific problematic code
    testMermaidCleaning(testCode) {
        console.log('=== Testing Mermaid Cleaning ===');
        console.log('Original code:');
        console.log(testCode);
        console.log('\nCleaned code:');
        const cleaned = this.cleanMermaidCode(testCode);
        console.log(cleaned);
        
        if (typeof mermaid !== 'undefined') {
            try {
                mermaid.parse(cleaned);
                console.log('✅ Cleaned code should parse successfully');
            } catch (error) {
                console.log('❌ Cleaned code still has parsing issues:', error.message);
                
                // Try aggressive cleaning
                const aggressiveCleaned = this.aggressiveCleanMermaidCode(testCode);
                console.log('\nAggressive cleaning:');
                console.log(aggressiveCleaned);
                
                try {
                    mermaid.parse(aggressiveCleaned);
                    console.log('✅ Aggressive cleaning should work');
                } catch (aggressiveError) {
                    console.log('❌ Even aggressive cleaning failed:', aggressiveError.message);
                }
            }
        } else {
            console.log('⚠️ Mermaid.js not loaded, cannot test parsing');
        }
    }
}

export default MarkdownRenderer;
