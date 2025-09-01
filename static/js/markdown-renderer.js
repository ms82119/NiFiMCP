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
            mermaid.initialize({
                startOnLoad: false,
                theme: 'default',
                flowchart: {
                    useMaxWidth: true,
                    htmlLabels: true
                }
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
                                <button class="mermaid-btn" onclick="window.app.markdownRenderer.renderMermaid('${diagramId}', \`${this.escapeHtml(code)}\`)">Re-render</button>
                                <button class="mermaid-btn" onclick="window.app.markdownRenderer.exportMermaid('${diagramId}', \`${this.escapeHtml(code)}\`)">Export SVG</button>
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
                        // Get the Mermaid code from the data attribute
                        const mermaidCode = container.getAttribute('data-mermaid-code');
                        if (mermaidCode) {
                            this.renderMermaid(container.id, mermaidCode);
                        }
                    }
                }
            });
        }, 100);
    }
    
    cleanMermaidCode(code) {
        // Remove HTML tags that might be in the code
        let cleaned = code.replace(/<br\s*\/?>/gi, '\n');  // Replace <br> with newlines
        cleaned = cleaned.replace(/<[^>]*>/g, '');  // Remove any other HTML tags
        
        // Fix common Mermaid syntax issues
        cleaned = cleaned.replace(/&lt;/g, '<');  // Fix escaped <
        cleaned = cleaned.replace(/&gt;/g, '>');  // Fix escaped >
        cleaned = cleaned.replace(/&amp;/g, '&');  // Fix escaped &
        
        // Fix node IDs with parentheses - replace with underscores
        cleaned = cleaned.replace(/\[([^\]]*\([^)]*\)[^\]]*)\]/g, (match, content) => {
            // Replace parentheses with underscores in node content
            const fixedContent = content.replace(/[()]/g, '_');
            return `[${fixedContent}]`;
        });
        
        // Fix node IDs with other problematic characters
        cleaned = cleaned.replace(/\[([^\]]*[^\w\s\-_][^\]]*)\]/g, (match, content) => {
            // Replace problematic characters with underscores
            const fixedContent = content.replace(/[^\w\s\-_]/g, '_');
            return `[${fixedContent}]`;
        });
        
        // Remove extra whitespace and normalize line endings
        cleaned = cleaned.trim();
        
        return cleaned;
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
            
            // Clear the container and render the diagram
            container.innerHTML = '';
            container.className = 'mermaid-container rendered';
            
            const { svg } = await mermaid.render(containerId + '-svg', cleanedCode);
            container.innerHTML = svg;
            
        } catch (error) {
            console.error('Error rendering Mermaid diagram:', error);
            const container = document.getElementById(containerId);
            if (container) {
                container.innerHTML = `<div class="mermaid-error">❌ Error rendering diagram: ${error.message}</div>`;
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
}

export default MarkdownRenderer;
