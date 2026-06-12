import sys
import subprocess

print("Forcing package upgrades for this exact Python environment...")

# This command finds the exact Python brain VS Code is using and forces it to upgrade the packages
subprocess.check_call([
    sys.executable, "-m", "pip", "install", "--upgrade", 
    "langchain", "langchain-google-genai", "langchain-core", "langchain-community"
])

print("\nSUCCESS! The environment is updated. You can now go back and run jarvis.py")