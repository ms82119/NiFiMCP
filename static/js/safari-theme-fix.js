/**
 * Safari Theme Fix - Ensures themes work properly in Safari
 */

class SafariThemeFix {
    constructor() {
        this.themePresets = {
            'modern-blue': {
                light: {
                    '--primary-gradient': 'linear-gradient(135deg, #3b82f6 0%, #1d4ed8 100%)',
                    '--primary-color': '#3b82f6',
                    '--primary-hover': '#2563eb',
                    '--secondary-color': '#64748b',
                    '--accent-color': '#3b82f6',
                    '--accent-hover': '#2563eb',
                    '--bg-primary': '#ffffff',
                    '--bg-secondary': '#f8fafc',
                    '--bg-tertiary': '#f1f5f9',
                    '--bg-chat': '#f8fafc',
                    '--text-primary': '#1e293b',
                    '--text-secondary': '#64748b',
                    '--text-muted': '#94a3b8',
                    '--border-color': '#e2e8f0',
                    '--border-light': '#f1f5f9',
                    '--shadow-color': 'rgba(0, 0, 0, 0.1)',
                    '--shadow-light': 'rgba(0, 0, 0, 0.05)',
                    '--success-color': '#10b981',
                    '--warning-color': '#f59e0b',
                    '--error-color': '#ef4444',
                    '--info-color': '#3b82f6'
                },
                dark: {
                    '--primary-gradient': 'linear-gradient(135deg, #3b82f6 0%, #1d4ed8 100%)',
                    '--primary-color': '#3b82f6',
                    '--primary-hover': '#60a5fa',
                    '--secondary-color': '#94a3b8',
                    '--accent-color': '#60a5fa',
                    '--accent-hover': '#93c5fd',
                    '--bg-primary': '#0f172a',
                    '--bg-secondary': '#1e293b',
                    '--bg-tertiary': '#334155',
                    '--bg-chat': '#1e293b',
                    '--text-primary': '#f8fafc',
                    '--text-secondary': '#cbd5e1',
                    '--text-muted': '#94a3b8',
                    '--border-color': '#334155',
                    '--border-light': '#475569',
                    '--shadow-color': 'rgba(0, 0, 0, 0.3)',
                    '--shadow-light': 'rgba(0, 0, 0, 0.2)',
                    '--success-color': '#10b981',
                    '--warning-color': '#f59e0b',
                    '--error-color': '#ef4444',
                    '--info-color': '#3b82f6'
                }
            },
            'professional-gray': {
                light: {
                    '--primary-gradient': 'linear-gradient(135deg, #6b7280 0%, #374151 100%)',
                    '--primary-color': '#6b7280',
                    '--primary-hover': '#4b5563',
                    '--secondary-color': '#6b7280',
                    '--accent-color': '#6b7280',
                    '--accent-hover': '#4b5563',
                    '--bg-primary': '#ffffff',
                    '--bg-secondary': '#f9fafb',
                    '--bg-tertiary': '#f3f4f6',
                    '--bg-chat': '#f9fafb',
                    '--text-primary': '#111827',
                    '--text-secondary': '#6b7280',
                    '--text-muted': '#9ca3af',
                    '--border-color': '#e5e7eb',
                    '--border-light': '#f3f4f6',
                    '--shadow-color': 'rgba(0, 0, 0, 0.1)',
                    '--shadow-light': 'rgba(0, 0, 0, 0.05)',
                    '--success-color': '#059669',
                    '--warning-color': '#d97706',
                    '--error-color': '#dc2626',
                    '--info-color': '#0284c7'
                },
                dark: {
                    '--primary-gradient': 'linear-gradient(135deg, #6b7280 0%, #374151 100%)',
                    '--primary-color': '#9ca3af',
                    '--primary-hover': '#d1d5db',
                    '--secondary-color': '#9ca3af',
                    '--accent-color': '#9ca3af',
                    '--accent-hover': '#d1d5db',
                    '--bg-primary': '#111827',
                    '--bg-secondary': '#1f2937',
                    '--bg-tertiary': '#374151',
                    '--bg-chat': '#1f2937',
                    '--text-primary': '#f9fafb',
                    '--text-secondary': '#d1d5db',
                    '--text-muted': '#9ca3af',
                    '--border-color': '#374151',
                    '--border-light': '#4b5563',
                    '--shadow-color': 'rgba(0, 0, 0, 0.3)',
                    '--shadow-light': 'rgba(0, 0, 0, 0.2)',
                    '--success-color': '#10b981',
                    '--warning-color': '#f59e0b',
                    '--error-color': '#ef4444',
                    '--info-color': '#3b82f6'
                }
            },
            'warm-orange': {
                light: {
                    '--primary-gradient': 'linear-gradient(135deg, #f97316 0%, #ea580c 100%)',
                    '--primary-color': '#f97316',
                    '--primary-hover': '#ea580c',
                    '--secondary-color': '#f97316',
                    '--accent-color': '#f97316',
                    '--accent-hover': '#ea580c',
                    '--bg-primary': '#ffffff',
                    '--bg-secondary': '#fff7ed',
                    '--bg-tertiary': '#fed7aa',
                    '--bg-chat': '#fff7ed',
                    '--text-primary': '#1c1917',
                    '--text-secondary': '#78716c',
                    '--text-muted': '#a8a29e',
                    '--border-color': '#fed7aa',
                    '--border-light': '#ffedd5',
                    '--shadow-color': 'rgba(0, 0, 0, 0.1)',
                    '--shadow-light': 'rgba(0, 0, 0, 0.05)',
                    '--success-color': '#16a34a',
                    '--warning-color': '#ca8a04',
                    '--error-color': '#dc2626',
                    '--info-color': '#0284c7'
                },
                dark: {
                    '--primary-gradient': 'linear-gradient(135deg, #f97316 0%, #ea580c 100%)',
                    '--primary-color': '#fb923c',
                    '--primary-hover': '#fdba74',
                    '--secondary-color': '#fb923c',
                    '--accent-color': '#fb923c',
                    '--accent-hover': '#fdba74',
                    '--bg-primary': '#1c1917',
                    '--bg-secondary': '#292524',
                    '--bg-tertiary': '#44403c',
                    '--bg-chat': '#292524',
                    '--text-primary': '#fef3c7',
                    '--text-secondary': '#d6d3d1',
                    '--text-muted': '#a8a29e',
                    '--border-color': '#44403c',
                    '--border-light': '#57534e',
                    '--shadow-color': 'rgba(0, 0, 0, 0.3)',
                    '--shadow-light': 'rgba(0, 0, 0, 0.2)',
                    '--success-color': '#22c55e',
                    '--warning-color': '#eab308',
                    '--error-color': '#ef4444',
                    '--info-color': '#3b82f6'
                }
            },
            'clean-green': {
                light: {
                    '--primary-gradient': 'linear-gradient(135deg, #10b981 0%, #059669 100%)',
                    '--primary-color': '#10b981',
                    '--primary-hover': '#059669',
                    '--secondary-color': '#10b981',
                    '--accent-color': '#10b981',
                    '--accent-hover': '#059669',
                    '--bg-primary': '#ffffff',
                    '--bg-secondary': '#f0fdf4',
                    '--bg-tertiary': '#dcfce7',
                    '--bg-chat': '#f0fdf4',
                    '--text-primary': '#14532d',
                    '--text-secondary': '#166534',
                    '--text-muted': '#16a34a',
                    '--border-color': '#dcfce7',
                    '--border-light': '#f0fdf4',
                    '--shadow-color': 'rgba(0, 0, 0, 0.1)',
                    '--shadow-light': 'rgba(0, 0, 0, 0.05)',
                    '--success-color': '#16a34a',
                    '--warning-color': '#ca8a04',
                    '--error-color': '#dc2626',
                    '--info-color': '#0284c7'
                },
                dark: {
                    '--primary-gradient': 'linear-gradient(135deg, #10b981 0%, #059669 100%)',
                    '--primary-color': '#34d399',
                    '--primary-hover': '#6ee7b7',
                    '--secondary-color': '#34d399',
                    '--accent-color': '#34d399',
                    '--accent-hover': '#6ee7b7',
                    '--bg-primary': '#0f172a',
                    '--bg-secondary': '#1e293b',
                    '--bg-tertiary': '#334155',
                    '--bg-chat': '#1e293b',
                    '--text-primary': '#f0fdf4',
                    '--text-secondary': '#bbf7d0',
                    '--text-muted': '#86efac',
                    '--border-color': '#334155',
                    '--border-light': '#475569',
                    '--shadow-color': 'rgba(0, 0, 0, 0.3)',
                    '--shadow-light': 'rgba(0, 0, 0, 0.2)',
                    '--success-color': '#22c55e',
                    '--warning-color': '#eab308',
                    '--error-color': '#ef4444',
                    '--info-color': '#3b82f6'
                }
            },
            'minimalist': {
                light: {
                    '--primary-gradient': 'linear-gradient(135deg, #000000 0%, #374151 100%)',
                    '--primary-color': '#000000',
                    '--primary-hover': '#374151',
                    '--secondary-color': '#6b7280',
                    '--accent-color': '#000000',
                    '--accent-hover': '#374151',
                    '--bg-primary': '#ffffff',
                    '--bg-secondary': '#ffffff',
                    '--bg-tertiary': '#f9fafb',
                    '--bg-chat': '#ffffff',
                    '--text-primary': '#000000',
                    '--text-secondary': '#6b7280',
                    '--text-muted': '#9ca3af',
                    '--border-color': '#e5e7eb',
                    '--border-light': '#f3f4f6',
                    '--shadow-color': 'rgba(0, 0, 0, 0.05)',
                    '--shadow-light': 'rgba(0, 0, 0, 0.02)',
                    '--success-color': '#000000',
                    '--warning-color': '#6b7280',
                    '--error-color': '#dc2626',
                    '--info-color': '#6b7280'
                },
                dark: {
                    '--primary-gradient': 'linear-gradient(135deg, #1f2937 0%, #374151 100%)',
                    '--primary-color': '#1f2937',
                    '--primary-hover': '#374151',
                    '--secondary-color': '#9ca3af',
                    '--accent-color': '#1f2937',
                    '--accent-hover': '#374151',
                    '--bg-primary': '#000000',
                    '--bg-secondary': '#111827',
                    '--bg-tertiary': '#1f2937',
                    '--bg-chat': '#111827',
                    '--text-primary': '#ffffff',
                    '--text-secondary': '#d1d5db',
                    '--text-muted': '#9ca3af',
                    '--border-color': '#374151',
                    '--border-light': '#4b5563',
                    '--shadow-color': 'rgba(0, 0, 0, 0.5)',
                    '--shadow-light': 'rgba(0, 0, 0, 0.3)',
                    '--success-color': '#ffffff',
                    '--warning-color': '#d1d5db',
                    '--error-color': '#ef4444',
                    '--info-color': '#d1d5db'
                }
            },
            'dark-purple': {
                light: {
                    '--primary-gradient': 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)',
                    '--primary-color': '#667eea',
                    '--primary-hover': '#5a67d8',
                    '--secondary-color': '#667eea',
                    '--accent-color': '#667eea',
                    '--accent-hover': '#5a67d8',
                    '--bg-primary': '#ffffff',
                    '--bg-secondary': '#f8fafc',
                    '--bg-tertiary': '#f1f5f9',
                    '--bg-chat': '#f8fafc',
                    '--text-primary': '#1e293b',
                    '--text-secondary': '#64748b',
                    '--text-muted': '#94a3b8',
                    '--border-color': '#e2e8f0',
                    '--border-light': '#f1f5f9',
                    '--shadow-color': 'rgba(0, 0, 0, 0.1)',
                    '--shadow-light': 'rgba(0, 0, 0, 0.05)',
                    '--success-color': '#10b981',
                    '--warning-color': '#f59e0b',
                    '--error-color': '#ef4444',
                    '--info-color': '#3b82f6'
                },
                dark: {
                    '--primary-gradient': 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)',
                    '--primary-color': '#8b5cf6',
                    '--primary-hover': '#a78bfa',
                    '--secondary-color': '#8b5cf6',
                    '--accent-color': '#8b5cf6',
                    '--accent-hover': '#a78bfa',
                    '--bg-primary': '#0f0f23',
                    '--bg-secondary': '#1a1a2e',
                    '--bg-tertiary': '#16213e',
                    '--bg-chat': '#1a1a2e',
                    '--text-primary': '#e2e8f0',
                    '--text-secondary': '#cbd5e1',
                    '--text-muted': '#94a3b8',
                    '--border-color': '#334155',
                    '--border-light': '#475569',
                    '--shadow-color': 'rgba(0, 0, 0, 0.4)',
                    '--shadow-light': 'rgba(0, 0, 0, 0.2)',
                    '--success-color': '#10b981',
                    '--warning-color': '#f59e0b',
                    '--error-color': '#ef4444',
                    '--info-color': '#3b82f6'
                }
            }
        };
    }
    
    applyTheme(preset, mode) {
        console.log('SafariThemeFix: Applying theme', preset, mode);
        
        const theme = this.themePresets[preset];
        if (!theme) {
            console.error('Theme preset not found:', preset);
            return;
        }
        
        const themeVars = theme[mode];
        if (!themeVars) {
            console.error('Theme mode not found:', mode);
            return;
        }
        
        const root = document.documentElement;
        
        // Remove all existing theme classes
        const allThemeClasses = [
            'theme-modern-blue', 'theme-professional-gray', 'theme-warm-orange',
            'theme-clean-green', 'theme-minimalist', 'theme-dark-purple',
            'mode-light', 'mode-dark'
        ];
        allThemeClasses.forEach(className => {
            root.classList.remove(className);
        });
        
        // Add the new theme class
        root.classList.add(`theme-${preset}`);
        root.classList.add(`mode-${mode}`);
        
        // Apply CSS variables directly to the root element
        Object.entries(themeVars).forEach(([property, value]) => {
            root.style.setProperty(property, value);
            console.log('Set CSS variable:', property, '=', value);
        });
        
        // Also set the attributes for CSS selectors
        root.setAttribute('data-theme', mode);
        root.setAttribute('data-theme-preset', preset);
        
        // Force Safari to re-render by temporarily changing a property
        const originalDisplay = root.style.display;
        root.style.display = 'none';
        root.offsetHeight; // Force reflow
        root.style.display = originalDisplay;
        
        // Additional Safari-specific fixes
        setTimeout(() => {
            // Force a style recalculation
            const body = document.body;
            if (body) {
                body.style.transform = 'translateZ(0)';
                body.offsetHeight; // Force reflow
                body.style.transform = '';
            }
            
            // Try to force CSS variable recalculation
            const testElement = document.createElement('div');
            testElement.style.setProperty('--test-var', '1px');
            root.appendChild(testElement);
            root.removeChild(testElement);
        }, 10);
        
        console.log('Theme applied successfully with forced re-render and Safari fixes');
    }
    
    isSafari() {
        return /^((?!chrome|android).)*safari/i.test(navigator.userAgent);
    }
}

// Export for use in other modules
window.SafariThemeFix = SafariThemeFix;
