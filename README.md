# Odoo IT Management Suite

![Odoo 18](https://img.shields.io/badge/Odoo-18.0-714B67?logo=odoo&logoColor=white)
![Python](https://img.shields.io/badge/Python-3.10+-3776AB?logo=python&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16-4169E1?logo=postgresql&logoColor=white)
![Status](https://img.shields.io/badge/Status-In--Migration-orange)

A scalable and modular IT Management solution built for **Odoo 18**. This project is designed to handle IT operations starting from department management to asset tracking with a high degree of extensibility.

## 🚀 Key Features

-   **Modular Architecture**: Built with a "Core & Extensions" approach for maximum scalability.
-   **IT Department Core**: Centralized management of IT units, codes, and managers.
-   **Asset Management**: Specialized tracking of hardware, software, and licenses with assignment status.
-   **Dockerized Environment**: Ready-to-go environment for development and production.

---

## 🏗 Project Structure

This project follows the Odoo Best Practices for modular development:

-   **[`it_department`](/addons/it_department)**: The **Core Module**. Contains base configurations, menus, and centralized security for all IT-related operations.
-   **[`it_department_asset`](/addons/it_department_asset)**: A sub-module for specialized Asset Management. Depends on the Core module.
-   **`it_asset` (Legacy)**: The original monolithic module, currently running on server but scheduled for deprecation in favor of the new modular structure.

---

## 🛠 Tech Stack

-   **Framework**: Odoo 18
-   **Language**: Python 3.10+
-   **Frontend**: XML (Odoo views & actions)
-   **Database**: PostgreSQL
-   **Orchestration**: Docker & Docker-Compose

---

## ⚙️ Getting Started

### Prerequisites
-   Docker and Docker Compose
-   Odoo 18 compatible database

### Local Development
1. Clone the repository.
2. Ensure your `docker-compose.yml` points to the correct database credentials.
3. Start the containers:
   ```bash
   docker-compose up -d
   ```
4. Access Odoo at `http://localhost:8069`.
5. Update the App list and install **IT Department Core** (`it_department`).

---

## 📋 Roadmap & Migration
This project is currently in the process of migrating logic from `it_asset` to the new `it_department` ecosystem.
- [x] Create IT Department Core.
- [x] Create IT Department Asset (Sub-module).
- [ ] Migrate Master Data from Legacy.
- [ ] Deprecate `it_asset` module.

---

## 📄 Documentation
Detailed technical documentation and directory mapping can be found in [DOCUMENTATION.MD](./DOCUMENTATION.MD).

---

## 🤝 Maintenance
Designed and maintained with focus on Odoo modularity and clean code.
