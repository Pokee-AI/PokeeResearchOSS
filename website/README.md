# Wazir Ahmed International L.L.C - Website

A professional website for Wazir Ahmed International L.L.C, a premier garage selling quality used car spare parts in Muscat, Oman.

## Features

- **Responsive Design**: Fully responsive layout that works on all devices (desktop, tablet, mobile)
- **Modern UI**: Clean and professional design with smooth animations
- **Parts Catalog**: Comprehensive listing of available spare parts with search and filter functionality
- **Contact Form**: Easy-to-use contact form for customer inquiries
- **Service Information**: Details about services offered
- **Company Information**: About section with company details and features

## Website Sections

1. **Home**: Hero section with call-to-action buttons
2. **About Us**: Company information and key features
3. **Spare Parts**: Catalog of available parts with search and category filters
4. **Services**: Information about services offered
5. **Contact**: Contact form and business information

## Parts Categories

- Engine Components
- Transmission Systems
- Suspension Parts
- Brake Systems
- Electrical Components
- Body Parts
- Interior Parts
- Cooling Systems
- Fuel Systems
- AC & Heating
- Steering Systems
- Wheels & Tires

## Technologies Used

- HTML5
- CSS3 (with CSS Grid and Flexbox)
- Vanilla JavaScript
- Font Awesome Icons (CDN)

## Installation & Setup

### Prerequisites

- Node.js (version 14 or higher)
- npm (comes with Node.js)

### Quick Start

1. Navigate to the website directory:
   ```bash
   cd website
   ```

2. Install dependencies (optional, for development server):
   ```bash
   npm install
   ```

3. Start the development server:
   ```bash
   npm start
   ```
   Or simply open `index.html` in your web browser.

### Alternative: Direct Browser Opening

You can also directly open the `index.html` file in any modern web browser without running a server.

## File Structure

```
website/
├── index.html          # Main HTML file
├── styles.css          # CSS styles
├── script.js           # JavaScript functionality
├── package.json        # Node.js package configuration
└── README.md          # This file
```

## Customization

### Updating Contact Information

Edit the contact section in `index.html` (around line 300) to update:
- Phone number
- Email address
- Physical address
- Working hours

### Adding More Parts

To add more spare parts to the catalog, add new `.part-card` elements in the `#partsGrid` section of `index.html`:

```html
<div class="part-card" data-category="your-category">
    <div class="part-icon">
        <i class="fas fa-your-icon"></i>
    </div>
    <h3>Part Name</h3>
    <p>Part description</p>
    <a href="#contact" class="btn-small">Inquire Now</a>
</div>
```

### Changing Colors

Update the CSS variables in `styles.css` (lines 12-18):

```css
:root {
    --primary-color: #c8102e;    /* Main brand color */
    --secondary-color: #1a1a1a;  /* Dark color */
    --accent-color: #ffd700;     /* Accent/highlight color */
    --text-color: #333;          /* Main text color */
    --light-gray: #f5f5f5;       /* Background color */
}
```

## Features Explained

### Search & Filter
- Real-time search across all parts
- Category-based filtering
- Combined search and filter functionality

### Responsive Navigation
- Desktop: Horizontal navigation bar
- Mobile: Hamburger menu with slide-in navigation

### Contact Form
- Client-side validation
- Form submission handling (currently shows alert, needs backend integration)

### Smooth Scrolling
- Automatic smooth scroll to sections when clicking navigation links
- Active section highlighting in navigation

## Browser Compatibility

- Chrome (latest)
- Firefox (latest)
- Safari (latest)
- Edge (latest)
- Mobile browsers (iOS Safari, Chrome Mobile)

## Future Enhancements

Consider adding:
- Backend integration for contact form
- Database for parts inventory
- Admin panel for managing parts
- Shopping cart functionality
- Online payment integration
- Multi-language support (Arabic/English)
- Image gallery for parts
- Customer testimonials section
- Live chat support

## Deployment

### Static Hosting Options

1. **Vercel**
   ```bash
   npm install -g vercel
   vercel
   ```

2. **Netlify**
   - Drag and drop the `website` folder to netlify.com/drop

3. **GitHub Pages**
   - Push to GitHub repository
   - Enable GitHub Pages in repository settings

4. **Traditional Web Hosting**
   - Upload all files via FTP/cPanel to your web hosting server

## Support

For technical support or inquiries about the website, please contact:
- Email: info@wazirahmed.com
- Phone: +968 1234 5678

## License

Copyright 2024 Wazir Ahmed International L.L.C. All rights reserved.

---

Built with care for Wazir Ahmed International L.L.C
