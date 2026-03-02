import subprocess

class SSHClient:
    def __init__(self, host):
        self.host = host

    def run(self, command):
        full_cmd = f'ssh {self.host} "{command}"'
        result = subprocess.run(full_cmd, shell=True, capture_output=True, text=True)
        return result.stdout.strip()