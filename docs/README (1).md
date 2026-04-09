# Fan XP — Landing Page

**Fan Experience, Upgraded.** A sports tech startup that detects real-time no-show seats at NBA games using Ticketmaster data, giving fans the signal to upgrade their seat before the final quarter.

---

## 🚀 Project Overview

**Fan XP** is a data-driven seat intelligence platform for NBA fans. By cross-referencing Ticketmaster listing snapshots taken 1 hour before tip-off and again at halftime, Fan XP identifies seats that were listed but never resold — confirmed no-shows — and surfaces them to fans in real time.

---

## ✅ Completed Features

### Landing Page Sections
- **Glassmorphism Navbar** — Fixed top nav with backdrop blur; becomes more opaque on scroll; mobile hamburger menu
- **Hero Section** — Full-viewport with animated particle canvas, basketball court line SVG overlay, gradient atmosphere, bold headline, two CTA buttons
- **Stats Bar** — 4 key metrics with count-up animation on scroll-into-view (12–18%, $200+, 30,000+, 1 hr)
- **How It Works** — 3-step process cards (Pre-Game Snapshot → Halftime Scan → Seat Signal) with step numbers, icons, and animated connector arrows
- **Feature Highlights** — 3 glassmorphism cards (Real-Time Intelligence, Court-Level Access, Instant Alerts)
- **Product Mockup** — Live dashboard card showing Barclays Center game with seat status table (Courtside / Lower Bowl / Upper Deck)
- **Waitlist CTA** — Email signup form with animated success state, city selector chips, disclaimer text
- **Footer** — Brand, social links, 4-column nav links, copyright bar

### Animations & Interactivity
- 🌟 **Floating particle canvas** — 110 animated dots in cyan/gold/white on hero background
- 📈 **Count-up stats** — Numbers animate from 0 to target when scrolled into view (cubic-easing)
- 👁️ **Scroll fade-in** — Every section/card uses `IntersectionObserver` for staggered fade-up entrance
- 🔔 **Navbar opacity** — Scrolled state triggers darker background + border
- ✨ **Button glow hovers** — Cyan glow pulse on all primary CTAs
- 📱 **Mobile menu** — Fullscreen overlay nav for small screens
- 🏙️ **City chip toggle** — Interactive city selection chips
- 📬 **Waitlist form** — Animated transition from form → success state on submit

---

## 📁 File Structure

```
index.html       — Complete single-file landing page (HTML + CSS + JS)
README.md        — Project documentation
```

---

## 🎨 Design System

| Token | Value |
|---|---|
| Background Primary | `#050A1A` (deep navy) |
| Background Secondary | `#0D1B2A` |
| Background Card | `#0F2035` |
| Accent Cyan | `#00F5FF` |
| Accent Gold | `#FFD700` |
| Text Primary | `#FFFFFF` |
| Text Muted | `rgba(255,255,255,0.55)` |
| Font | Inter, Rajdhani (Google Fonts) |

---

## 🌐 Entry Point

| Path | Description |
|---|---|
| `/index.html` | Main landing page (single page) |
| `#how-it-works` | 3-step process section |
| `#features` | Feature highlights |
| `#product` | Product mockup |
| `#waitlist` | Email waitlist CTA |

---

## 🔧 Technical Notes

- **Zero external dependencies** except Google Fonts CDN
- Pure CSS animations & JS (no libraries, no frameworks)
- Responsive / mobile-first breakpoints at 480px, 768px, 1024px
- Particle system: custom canvas-based `requestAnimationFrame` loop
- Waitlist form: client-side only (no backend); extend with a form service (Mailchimp, Formspree, ConvertKit) for real signups

---

## 🔜 Recommended Next Steps

1. **Connect Waitlist Form** — Integrate Formspree / Mailchimp / ConvertKit API for real email capture
2. **Add Logo Images** — Drop in generated logo SVGs/PNGs for wordmark, icon, and dark/light variants
3. **SEO Meta Tags** — Add Open Graph / Twitter Card meta tags for social sharing
4. **Analytics** — Add Plausible or Google Analytics script
5. **Team/Investor Page** — Add a "Team" section or a separate `/about` page
6. **Demo Video** — Embed a product walkthrough video in the mockup section
7. **Blog/Updates** — Add a `/blog` route for product announcements
8. **City-specific Pages** — Dynamic pages for each NBA market

---

## 📬 Deploy

To go live, use the **Publish tab** in the project editor. One-click deployment provides a live URL instantly.

---

*© 2025 Fan XP Inc. — Fan Experience, Upgraded.*
