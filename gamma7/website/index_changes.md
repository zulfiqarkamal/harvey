# Changes needed in gamma7.io index.html

These are the two additions needed in your existing `index.html`. The deployed site already has the `setLang()` JS function, so only the HTML snippets below need to be added.

---

## 1. Language toggle buttons (DA / EN)

Find the `<!-- NAV -->` comment (or the nav/header area) and add these buttons. If you have a `<nav>` element, place them inside it. If the nav area is empty, add:

```html
<!-- Language toggle — place inside or just after your nav/header -->
<div class="lang-toggle" role="group" aria-label="Sprog / Language" style="position:fixed;top:14px;right:max(16px,env(safe-area-inset-right,0px));z-index:200;display:flex;gap:4px;">
  <button class="lang-btn" data-lang="da" onclick="setLang('da')" aria-pressed="true">DA</button>
  <button class="lang-btn" data-lang="en" onclick="setLang('en')" aria-pressed="false">EN</button>
</div>
```

Add this CSS once (if not already in your stylesheet):

```css
.lang-toggle { display: flex; gap: 4px; }
.lang-btn {
  padding: 4px 10px;
  border-radius: 4px;
  border: 1px solid rgba(140,180,255,0.15);
  background: transparent;
  color: #5A87AD;
  font-size: 12px;
  font-weight: 500;
  cursor: pointer;
  transition: all .2s;
  font-family: inherit;
}
.lang-btn.active,
.lang-btn:hover {
  background: #00D4B4;
  border-color: #00D4B4;
  color: #0D1E37;
}
```

---

## 2. Compliance Agent link section

Add this block somewhere in the body — after the services section works well, or just before the contact/CTA section:

```html
<!-- Gamma7 Compliance Agent — link section -->
<section id="agent" style="background:#112445;padding:72px 0;">
  <div style="max-width:1100px;margin:0 auto;padding:0 max(16px,env(safe-area-inset-left,0px));display:grid;grid-template-columns:1fr 1fr;gap:48px;align-items:center;">
    <div>
      <p style="font-size:11px;font-weight:600;letter-spacing:.15em;text-transform:uppercase;color:#00D4B4;margin-bottom:12px;">
        <span data-da="AI-teknologi" data-en="AI technology">AI-teknologi</span>
      </p>
      <h2 style="font-family:'Cormorant',Georgia,serif;font-size:clamp(28px,4vw,42px);font-weight:600;margin-bottom:16px;line-height:1.15;">
        <span data-da="Gamma7 Compliance Agent" data-en="Gamma7 Compliance Agent">Gamma7 Compliance Agent</span>
      </h2>
      <p style="font-size:16px;color:#8DB4D6;line-height:1.65;margin-bottom:28px;">
        <span data-da="Vores AI-drevne compliance-agent analyserer automatisk dit regulatoriske landskab, identificerer mangler og udarbejder revisionsklare rapporter — baseret på DORA, GDPR og ICT-risikostyring." data-en="Our AI-powered compliance agent automatically analyses your regulatory landscape, identifies gaps and produces audit-ready reports — based on DORA, GDPR and ICT risk management.">Vores AI-drevne compliance-agent analyserer automatisk dit regulatoriske landskab, identificerer mangler og udarbejder revisionsklare rapporter — baseret på DORA, GDPR og ICT-risikostyring.</span>
      </p>
      <a href="/agent.html" style="display:inline-block;padding:12px 28px;background:#00D4B4;color:#0D1E37;font-weight:600;font-size:14px;border-radius:6px;text-decoration:none;">
        <span data-da="Læs mere om agenten" data-en="Learn more about the agent">Læs mere om agenten</span>
      </a>
    </div>
    <div style="background:#0a1628;border:1px solid rgba(140,180,255,0.10);border-radius:10px;padding:20px 22px;font-family:'Cascadia Code','Fira Code',monospace;font-size:13px;line-height:1.8;" aria-hidden="true">
      <div style="display:flex;gap:6px;margin-bottom:16px;">
        <span style="width:10px;height:10px;border-radius:50%;background:#ff5f57;display:inline-block;"></span>
        <span style="width:10px;height:10px;border-radius:50%;background:#febc2e;display:inline-block;"></span>
        <span style="width:10px;height:10px;border-radius:50%;background:#28c840;display:inline-block;"></span>
      </div>
      <div><span style="color:#5A87AD;">$</span> <span style="color:#00D4B4;">gamma7 assess --framework DORA</span></div>
      <div style="color:#5A87AD;">Analyserer kontraktregister…</div>
      <div style="color:#5aef80;">✓ 47 kontrakter gennemgået</div>
      <div style="color:#f0c040;">⚠ 3 kritiske mangler identificeret</div>
      <div style="color:#5A87AD;">  Art. 28 — Exit-strategi mangler</div>
      <div style="color:#5aef80;">✓ Revisionsrapport klar</div>
    </div>
  </div>
</section>
```

---

## 3. Remove topbar (if present in your local version)

If your local `index.html` has a topbar element (a banner above the main nav), remove the entire element and its associated CSS. The deployed version already has no topbar — only an empty `<!-- NAV -->` comment.

---

## Files to upload to gamma7.io root

| File | Purpose |
|------|---------|
| `privacy.html` | Privatlivspolitik DA/EN (footer link already works) |
| `disclaimer.html` | Vilkår og betingelser DA/EN (footer link already works) |
| `agent.html` | Compliance agent landing page |
| `index.html` | Update with lang toggle + agent section (manual edit per above) |
