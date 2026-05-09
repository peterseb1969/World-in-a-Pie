/** @type {import('tailwindcss').Config} */
// Brand tokens per docs/ui-guidance.md (CASE-302). The four-app converged
// palette + Inter. Apps SHOULD NOT introduce inline hex colors — extend
// this config or use the named tokens. Dark mode is a future cross-app
// project (not in v1).
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      colors: {
        primary: {
          DEFAULT: '#2B579A',  // Microsoft-blue — primary actions, focus, links
          light:   '#5B9BD5',  // Hover / softer emphasis
          dark:    '#1E3F6F',  // Pressed / strong contrast (optional)
        },
        accent:    '#ED7D31',  // Orange — sparing CTAs, highlights
        success:   '#2E8B57',  // Sea green — saved, OK, health
        danger:    '#DC3545',  // Bootstrap red — destructive, error
        surface:   '#FFFFFF',  // Card / modal / dialog background
        background:'#F8FAFC',  // Page background (slate-50-ish)
        text: {
          DEFAULT: '#333333',  // Body text
          muted:   '#999999',  // Captions, secondary labels
        },
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', 'sans-serif'],
      },
    },
  },
  plugins: [],
}
