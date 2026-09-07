# Scripts

This repository is intended for all kinds of scripts.

## 🚀 Quick Start
Get up and running in less than a minute.

```bash
# Clone the repository
git clone git@github.com:faldanarh/scripts.git

# Move into the directory
cd scripts
```

## 📁 Repository Structure
A quick overview of the file layout to help users navigate your code.
```text
├── scripts/               # Core executable scripts
│   ├── proactive_cases
│   │   ├── create_proactive_case.py  # Script for proactive case request
│   │   └── README.md                 # Script documentation
├── .env.example           # Template for environment configuration
├── README.md              # Project documentation
└── requirements.txt       # Software library dependencies (when exists)
```

## 🔒 Troubleshooting
Common errors and how to resolve them quickly.

* **Error:** `Permission denied` when running shell scripts.
  * **Fix:** Execute `chmod +x scripts/script_name.sh` to grant execution permissions.
* **Error:** `ModuleNotFoundError: No module named '...'`
  * **Fix:** Ensure your virtual environment is active and run `pip install -r requirements.txt`.

