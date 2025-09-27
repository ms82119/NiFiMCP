/**
 * Enhanced Theme Manager - Handles theme presets and light/dark mode
 */

class ThemeManager {
    constructor() {
        this.currentTheme = localStorage.getItem('theme') || 'light';
        this.currentPreset = localStorage.getItem('themePreset') || 'modern-blue';
        this.themePresets = [
            { id: 'modern-blue', name: 'Modern Blue', description: 'Clean and professional' },
            { id: 'professional-gray', name: 'Professional Gray', description: 'Corporate and neutral' },
            { id: 'warm-orange', name: 'Warm Orange', description: 'Energetic and friendly' },
            { id: 'clean-green', name: 'Clean Green', description: 'Natural and calming' },
            { id: 'minimalist', name: 'Minimalist', description: 'Simple and clean' },
            { id: 'dark-purple', name: 'Dark Purple', description: 'Original purple theme' }
        ];
        
        // Initialize Safari fix if needed
        this.safariFix = null;
        this.isSafari = /^((?!chrome|android).)*safari/i.test(navigator.userAgent);
        if (this.isSafari) {
            console.log('Safari detected, initializing Safari theme fix');
            this.safariFix = new SafariThemeFix();
        }
        
        this.init();
    }
    
    init() {
        this.applyTheme();
        this.setupEventListeners();
        this.updateThemeSelector();
    }
    
    setupEventListeners() {
        // Light/Dark mode toggle
        const themeToggle = document.getElementById('theme-toggle');
        if (themeToggle) {
            themeToggle.addEventListener('click', () => this.toggleTheme());
        }
        
        // Theme preset selector
        const themeSelectorBtn = document.getElementById('theme-selector-btn');
        const themeSelectorDropdown = document.getElementById('theme-selector-dropdown');
        
        if (themeSelectorBtn && themeSelectorDropdown) {
            // Safari-compatible event handling
            themeSelectorBtn.addEventListener('click', (e) => {
                e.preventDefault();
                e.stopPropagation();
                this.toggleThemeSelector();
            });
            
            // Also handle touch events for mobile Safari
            themeSelectorBtn.addEventListener('touchend', (e) => {
                e.preventDefault();
                e.stopPropagation();
                this.toggleThemeSelector();
            });
            
            // Close dropdown when clicking outside
            document.addEventListener('click', (e) => {
                if (!themeSelectorBtn.contains(e.target) && !themeSelectorDropdown.contains(e.target)) {
                    this.closeThemeSelector();
                }
            });
            
            // Theme option clicks
            const themeOptions = themeSelectorDropdown.querySelectorAll('.theme-option');
            themeOptions.forEach(option => {
                option.addEventListener('click', (e) => {
                    e.preventDefault();
                    e.stopPropagation();
                    const preset = option.getAttribute('data-theme-preset');
                    this.setThemePreset(preset);
                    this.closeThemeSelector();
                });
                
                // Also handle touch events for mobile Safari
                option.addEventListener('touchend', (e) => {
                    e.preventDefault();
                    e.stopPropagation();
                    const preset = option.getAttribute('data-theme-preset');
                    this.setThemePreset(preset);
                    this.closeThemeSelector();
                });
            });
        }
    }
    
    applyTheme() {
        console.log('Applying theme:', this.currentPreset, 'mode:', this.currentTheme);
        
        if (this.isSafari && this.safariFix) {
            // Use Safari-specific theme application
            this.safariFix.applyTheme(this.currentPreset, this.currentTheme);
        } else {
            // Standard theme application
            document.documentElement.setAttribute('data-theme', this.currentTheme);
            document.documentElement.setAttribute('data-theme-preset', this.currentPreset);
            
            // Force Safari to recognize the changes
            document.documentElement.style.setProperty('--force-refresh', Date.now());
        }
        
        this.updateThemeIcon();
        this.updateThemeSelector();
        
        // Debug: Check if attributes were set
        console.log('Theme attributes set:', {
            'data-theme': document.documentElement.getAttribute('data-theme'),
            'data-theme-preset': document.documentElement.getAttribute('data-theme-preset')
        });
    }
    
    toggleTheme() {
        this.currentTheme = this.currentTheme === 'light' ? 'dark' : 'light';
        localStorage.setItem('theme', this.currentTheme);
        // Use unified applyTheme so Safari path is honored
        this.applyTheme();
        
        // Re-render Mermaid diagrams for theme change
        this.reRenderMermaidDiagrams();
    }
    
    setThemePreset(presetId) {
        console.log('Setting theme preset to:', presetId);
        this.currentPreset = presetId;
        localStorage.setItem('themePreset', this.currentPreset);
        
        // Apply the theme using the appropriate method
        this.applyTheme();
        
        // Re-render Mermaid diagrams for theme change
        this.reRenderMermaidDiagrams();
        
        // Debug: Check if the preset was applied
        console.log('Theme preset applied:', {
            'data-theme-preset': document.documentElement.getAttribute('data-theme-preset'),
            'currentPreset': this.currentPreset
        });
    }
    
    toggleThemeSelector() {
        const dropdown = document.getElementById('theme-selector-dropdown');
        if (dropdown) {
            dropdown.classList.toggle('show');
        }
    }
    
    closeThemeSelector() {
        const dropdown = document.getElementById('theme-selector-dropdown');
        if (dropdown) {
            dropdown.classList.remove('show');
        }
    }
    
    updateThemeIcon() {
        const themeIcon = document.querySelector('.theme-icon');
        if (themeIcon) {
            themeIcon.textContent = this.currentTheme === 'light' ? '☀️' : '🌙';
        }
    }
    
    // Re-render Mermaid diagrams when theme changes
    async reRenderMermaidDiagrams() {
        // Wait a bit for the theme to be applied
        setTimeout(async () => {
            if (window.app && window.app.markdownRenderer) {
                try {
                    await window.app.markdownRenderer.reRenderAllMermaidDiagrams();
                } catch (error) {
                    console.error('Error re-rendering Mermaid diagrams:', error);
                }
            }
        }, 100);
    }
    
    updateThemeSelector() {
        const themeNameSpan = document.querySelector('#theme-selector-btn .theme-name');
        const themePreview = document.querySelector('#theme-selector-btn .theme-preview');
        const themeOptions = document.querySelectorAll('.theme-option');
        
        // Update current theme name
        const currentPreset = this.themePresets.find(p => p.id === this.currentPreset);
        if (themeNameSpan && currentPreset) {
            themeNameSpan.textContent = currentPreset.name;
        }
        
        // Update active theme option
        themeOptions.forEach(option => {
            option.classList.remove('active');
            if (option.getAttribute('data-theme-preset') === this.currentPreset) {
                option.classList.add('active');
            }
        });
    }
    
    getCurrentTheme() {
        return this.currentTheme;
    }
    
    getCurrentPreset() {
        return this.currentPreset;
    }
    
    getAvailablePresets() {
        return this.themePresets;
    }
}

export default ThemeManager;
