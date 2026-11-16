# Local Startup Guide: PMO Stack & PMO Dashboard

Simple commands to start, check, and stop both applications locally on your Mac.

---

## 1. Application 1: NI PMO Stack (Tela Insights + Dashboards)

Located at: `~/Desktop/ni-pmo-stack`

### Start
Open Terminal and run:
```bash
cd ~/Desktop/ni-pmo-stack
./stack.sh start
```

### Check Status
```bash
cd ~/Desktop/ni-pmo-stack
./stack.sh status
```

### Access URLs
- **Dashboards:** [http://localhost:28903](http://localhost:28903)
- **Tela Screens:** [http://localhost:28902](http://localhost:28902)
- **Backend API:** [http://localhost:28901](http://localhost:28901)

### Stop
```bash
cd ~/Desktop/ni-pmo-stack
./stack.sh stop
```

---

## 2. Application 2: PMO Dashboard (Enterprise Docker Stack)

Located at: `~/Desktop/pmo_bashboard-master`

### Prerequisites
Make sure Colima / Docker is running:
```bash
colima status || colima start
```

### Start
Open Terminal and run:
```bash
cd ~/Desktop/pmo_bashboard-master
docker compose -f deploy/compose/compose.yaml --env-file deploy/compose/secrets/.env --profile core --profile dev up -d
```

### Check Status
```bash
cd ~/Desktop/pmo_bashboard-master
docker compose -f deploy/compose/compose.yaml ps
```

### Access URLs & Login
- **URL:** [http://tela.localhost:8088](http://tela.localhost:8088)
- **Username:** `admin`
- **Password:** View with:
  ```bash
  cat ~/Desktop/pmo_bashboard-master/deploy/compose/secrets/dev_admin_password
  ```

### Stop
```bash
cd ~/Desktop/pmo_bashboard-master
docker compose -f deploy/compose/compose.yaml down
```

---

## Summary Reference Table

| App | Directory | Start Command | Stop Command | Access URL |
|---|---|---|---|---|
| **NI PMO Stack** | `~/Desktop/ni-pmo-stack` | `./stack.sh start` | `./stack.sh stop` | `http://localhost:28903` |
| **PMO Dashboard** | `~/Desktop/pmo_bashboard-master` | `docker compose -f deploy/compose/compose.yaml --env-file deploy/compose/secrets/.env --profile core --profile dev up -d` | `docker compose -f deploy/compose/compose.yaml down` | `http://tela.localhost:8088` |
