# Wazir Ahmed International L.L.C - Garage Website

This repository now contains a professional website for **Wazir Ahmed International L.L.C**, a garage specializing in quality used car spare parts located in Muscat, Sultanate of Oman.

## Project Structure

```
/vercel/sandbox/
├── website/                    # Main website directory
│   ├── index.html             # Homepage (single-page website)
│   ├── styles.css             # CSS styling
│   ├── script.js              # JavaScript functionality
│   ├── package.json           # Node.js configuration
│   ├── README.md              # Detailed website documentation
│   └── .gitignore             # Git ignore file
├── WEBSITE_README.md          # This file
└── [other repository files]   # Original PokeeResearch files
```

## Quick Start

### Option 1: Open Directly in Browser (Simplest)

1. Navigate to the website folder:
   ```bash
   cd /vercel/sandbox/website
   ```

2. Open `index.html` in your web browser:
   ```bash
   # On Linux
   xdg-open index.html

   # Or simply copy the file path and open it in any browser
   ```

### Option 2: Using Node.js Server (Recommended for Development)

1. Navigate to the website folder:
   ```bash
   cd /vercel/sandbox/website
   ```

2. Install dependencies:
   ```bash
   npm install
   ```

3. Start the server:
   ```bash
   npm start
   ```
   The website will open automatically in your browser at `http://localhost:8080`

### Option 3: Using Python Server (If Node.js not available)

```bash
cd /vercel/sandbox/website
python3 -m http.server 8080
```
Then open http://localhost:8080 in your browser.

## Website Features

### 1. Home Section
- Eye-catching hero section with company branding
- Call-to-action buttons for browsing parts and contact
- Responsive design for all devices

### 2. About Us
- Company introduction and mission
- Key features highlighting:
  - Quality Assured Parts
  - Competitive Prices
  - Large Inventory
  - Expert Support

### 3. Spare Parts Catalog
- 12+ categories of car parts
- Real-time search functionality
- Category filtering
- Interactive part cards with hover effects

**Available Part Categories:**
- Engine Components
- Transmission Systems
- Suspension Parts
- Brake Systems
- Electrical Components
- Body Parts
- Interior Parts
- Cooling Systems
- Fuel Systems
- AC & Heating Systems
- Steering Systems
- Wheels & Tires

### 4. Services Section
- Part Sourcing Service
- Quality Inspection
- Delivery Options
- Warranty Coverage

### 5. Contact Section
- Contact form for inquiries
- Business information:
  - Location: Muscat, Oman
  - Phone: +968 1234 5678 (placeholder - update with real number)
  - Email: info@wazirahmed.com (placeholder - update with real email)
  - Working Hours: Sat-Thu 8AM-6PM, Friday Closed

## Customization Guide

### Update Contact Information

Edit `website/index.html` around line 300-350 to update:
- Phone number
- Email address
- Physical address (specific location in Muscat)
- Working hours
- Google Maps integration (if needed)

### Update Company Colors

Edit `website/styles.css` at the beginning (CSS variables):
```css
:root {
    --primary-color: #c8102e;    /* Main red color */
    --secondary-color: #1a1a1a;  /* Dark background */
    --accent-color: #ffd700;     /* Gold accent */
}
```

### Add Company Logo

1. Add your logo image to the `website/` directory
2. Update the logo section in `index.html`:
   ```html
   <div class="logo">
       <img src="your-logo.png" alt="Wazir Ahmed International">
       <span>Wazir Ahmed International L.L.C</span>
   </div>
   ```

### Add Real Part Images

Update the part cards in `index.html` to include actual images:
```html
<div class="part-card" data-category="engine">
    <img src="images/engine-part.jpg" alt="Engine Parts">
    <h3>Engine Components</h3>
    <p>Description...</p>
</div>
```

## Technical Specifications

- **Framework**: Vanilla HTML, CSS, JavaScript (no dependencies)
- **Icons**: Font Awesome 6.4.0 (CDN)
- **Responsive**: Mobile-first design with CSS Grid and Flexbox
- **Browser Support**: All modern browsers (Chrome, Firefox, Safari, Edge)
- **Performance**: Lightweight, fast loading
- **SEO**: Semantic HTML, meta tags included

## Deployment Options

### 1. Vercel (Recommended)
```bash
npm install -g vercel
cd /vercel/sandbox/website
vercel
```

### 2. Netlify
- Drag and drop the `website` folder to netlify.com/drop
- Or connect your Git repository

### 3. Traditional Web Hosting
- Upload files via FTP/cPanel to your hosting provider
- Ensure `index.html` is in the root directory

### 4. GitHub Pages
```bash
git add website/
git commit -m "Add garage website"
git push origin main
```
Then enable GitHub Pages in repository settings.

## Next Steps & Enhancements

Consider implementing:

1. **Backend Integration**
   - Contact form submission to email or database
   - Parts inventory management system
   - Admin panel for updating parts

2. **Enhanced Features**
   - Multi-language support (English/Arabic)
   - Shopping cart and checkout
   - User accounts and order history
   - Real-time inventory status
   - Part image gallery
   - Customer reviews and testimonials

3. **Business Features**
   - Online quotation system
   - WhatsApp integration for quick inquiries
   - Google Maps integration
   - Social media links and integration
   - Blog/News section

4. **Analytics**
   - Google Analytics integration
   - Conversion tracking
   - User behavior analysis

## Support & Contact

For questions about the website:
- Technical Support: Review the detailed README in `website/README.md`
- Business Inquiries: Contact Wazir Ahmed International L.L.C

## License

Copyright 2024 Wazir Ahmed International L.L.C. All rights reserved.

---

**Note**: This website has been created as a professional starting point. Remember to:
- Update all placeholder contact information with real details
- Add your company logo
- Customize colors to match your branding
- Add actual images of your parts inventory
- Set up a backend for the contact form
- Test thoroughly before going live
