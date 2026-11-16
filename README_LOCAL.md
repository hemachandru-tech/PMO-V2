# PMO Dashboard & NI PMO Stack: Parallel Testing Guide

Both applications are configured and running in parallel against your newly imported MySQL database (`tpg`) containing all 35 TPG Dataverse tables (111 projects).

---

## 1. Application 1: PMO Dashboard (Enterprise Stack)

- **Web App URL:** [http://tela.localhost:8088](http://tela.localhost:8088)
- **Database Connection:** `tpg-mysql` (MySQL database: `tpg`)
- **Login Credentials:**
  - **Username:** `admin`
  - **Password:** `daDLKr3oChUHJi7IFfP1HPcRCPKxU2rMNr6Ll14o`

### How to test on the website:
1. Open [http://tela.localhost:8088](http://tela.localhost:8088) in your browser.
2. Sign in as `admin`.
3. Click **"Ask"** in the top navigation bar.
4. Select or ensure the connection is set to **`tpg-mysql`**.
5. Ask any question, for example:
   - *"How many projects are there in tpg_project?"*
   - *"Show total projects by status report RAG indicator"*
   - *"List project changes and their types"*

### Terminal Commands:
- **Start:** `cd ~/Desktop/pmo_bashboard-master && docker compose -f deploy/compose/compose.yaml --env-file deploy/compose/secrets/.env --profile core --profile dev up -d`
- **Stop:** `cd ~/Desktop/pmo_bashboard-master && docker compose -f deploy/compose/compose.yaml down`

---

## 2. Application 2: NI PMO Stack (Tela Insights & Dashboards)

- **Tela Screens (Talk to Data):** [http://localhost:28902](http://localhost:28902)
- **PMO Dashboards:** [http://localhost:28903](http://localhost:28903)
- **Database Connection:** `mysql:127.0.0.1:3306:tpg`
- **Login Credentials:**
  - **Username:** `admin`
  - **Password:** `lQBoJfe1XHig1PEKtzc`

### How to test on the website:
1. Open [http://localhost:28902](http://localhost:28902) in your browser.
2. Sign in as `admin`.
3. In the sidebar or search, select **"Talk to Data" / "Ask"**.
4. Ask any question against `tpg` (e.g. *"How many projects are there in tpg_project?"*).

### Terminal Commands:
- **Start:** `cd ~/Desktop/ni-pmo-stack && ./stack.sh start`
- **Stop:** `cd ~/Desktop/ni-pmo-stack && ./stack.sh stop`
- **Status:** `cd ~/Desktop/ni-pmo-stack && ./stack.sh status`

---

## 3. Database Credentials Reference (MySQL)

- **Host:** `127.0.0.1` (or `host.docker.internal` from inside Docker)
- **Port:** `3306`
- **Database:** `tpg` (Loaded with all 35 TPG Dataverse tables from `tpg_dataverse_mysql_1.sql`)
- **Admin User:** `root` (Password: `1234`)
- **Read-Only User (for PMO Dashboard security):** `tpg_ro` (Password: `1234`)
