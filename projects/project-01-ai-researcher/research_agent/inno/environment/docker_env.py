import os
import os.path as osp
import subprocess
import codecs
from research_agent.constant import BASE_IMAGES, AI_USER, GITHUB_AI_TOKEN, GPUS, PLATFORM
import time
import socket
import json
from pathlib import Path
import shutil
wd = Path(__file__).parent.resolve()
from dataclasses import dataclass, field
from typing import Optional, Union, Dict
from functools import update_wrapper
from inspect import signature
@dataclass
class DockerConfig: 
    container_name: str
    workplace_name: str 
    communication_port: int # 12345
    test_pull_name: str = field(default='main')
    task_name: Optional[str] = field(default=None)
    git_clone: bool = field(default=False)
    setup_package: Optional[str] = field(default=None)
    local_root: str = field(default=os.getcwd())
    

class DockerEnv:
    def __init__(self, config: Union[DockerConfig, Dict]):
        if isinstance(config, Dict):
            config = DockerConfig(**config)
        self.workplace_name = config.workplace_name
        self.local_workplace = osp.join(config.local_root, config.workplace_name)
        self.docker_workplace = f"/{config.workplace_name}"
        self.container_name = config.container_name
        self.test_pull_name = config.test_pull_name
        self.task_name = config.task_name
        self.git_clone = config.git_clone
        self.setup_package = config.setup_package
        self.communication_port = config.communication_port
        
    def init_container(self):
        container_check_command = ["docker", "ps", "-a", "--filter", f"name={self.container_name}", "--format", "{{.Names}}"]
        existing_container = subprocess.run(container_check_command, capture_output=True, text=True)
        os.makedirs(self.local_workplace, exist_ok=True)
        
        if self.setup_package is not None:
            unzip_command = ["tar", "-xzvf", f"packages/{self.setup_package}.tar.gz", "-C", self.local_workplace]
            subprocess.run(unzip_command)
        if self.git_clone:
            metachain_dir = os.path.join(self.local_workplace, 'metachain')
            if not os.path.exists(metachain_dir):
                clone_url = f"https://{AI_USER}:{GITHUB_AI_TOKEN}@github.com/tjb-tech/metachain.git"
                # shell=False + explicit cwd: avoids shell injection via task/branch names
                # and keeps the credential out of any shell string.
                git_command = ["git", "clone", "-b", self.test_pull_name, clone_url, "metachain"]
                result = subprocess.run(git_command, cwd=self.local_workplace, capture_output=True, text=True)
                if result.returncode != 0:
                    raise Exception(f"Failed to clone the repository. Please check your internet connection and try again.")
                # create a new branch
            new_branch_name = f"{self.test_pull_name}_{self.task_name}"
            result = subprocess.run(["git", "checkout", "-b", new_branch_name], cwd=metachain_dir, capture_output=True, text=True)
            if result.returncode != 0:
                print(f"Failed to create and switch to new branch. Error: {result.stderr}")
                result = subprocess.run(["git", "checkout", new_branch_name], cwd=metachain_dir, capture_output=True, text=True)
                if result.returncode != 0:
                    raise Exception(f"Failed to switch to new branch. Error: {result.stderr}")
                else:
                    print(f"Successfully switched to new branch: {new_branch_name}")
            else:
                print(f"Successfully created and switched to new branch: {new_branch_name}")

        if existing_container.stdout.strip() == self.container_name:
            # check if the container is running
            running_check_command = ["docker", "ps", "--filter", f"name={self.container_name}", "--format", "{{.Names}}"]
            running_container = subprocess.run(running_check_command, capture_output=True, text=True)

            if running_container.stdout.strip() == self.container_name:
                print(f"Container '{self.container_name}' is already running. Skipping creation.")
                self.wait_for_container_ready(timeout=60)
                return  # container is already running, skip creation
            else:
                # container exists but is not running, start it
                start_command = ["docker", "start", self.container_name]
                subprocess.run(start_command)
                print(f"Container '{self.container_name}' has been started.")
                self.wait_for_container_ready(timeout=60)
                return
        
        # if the container does not exist, create and start a new container
        gpu_cmd = ["--gpus", GPUS] if GPUS else []
        docker_command = [
            "docker", "run", "-d", "--platform", PLATFORM, "--userns=host",] + gpu_cmd + ["--name", self.container_name, 
            "--user", "root", "-v", f"{self.local_workplace}:{self.docker_workplace}",
            "-w", f"{self.docker_workplace}", "-p", f"{self.communication_port}:8000", 
            "--restart", "unless-stopped", BASE_IMAGES
        ]
        print(docker_command)
        # execute the docker command
        result = subprocess.run(docker_command, capture_output=True, text=True)
        if result.returncode != 0:
            raise Exception(f"Failed to start container: {result.stderr}")
        if self.wait_for_container_ready(timeout=60):
            print(f"Container '{self.container_name}' has been created and started.")
    def wait_for_container_ready(self, timeout=30):
        """using subprocess to check if the container is running"""
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            result = subprocess.run(
                ["docker", "inspect", "--format", "{{.State.Running}}", self.container_name],
                capture_output=True,
                text=True
            )
            print("result.returncode", result.returncode)
            print("result.stdout", result.stdout)
            
            if result.returncode == 0 and "true" in result.stdout.lower():
                # 额外检查 tcp_server 是否运行
                try:
                    port_info = check_container_ports(self.container_name)
                    if not port_info or port_info[1] != 8000:
                        raise RuntimeError("No published host port found for container TCP port 8000")
                    available_port = port_info[0]
                    self.communication_port = available_port
                    result = self.run_command('ps aux')
                    print("result", result)
                    if "tcp_server.py" in result['result']:
                        return True
                except Exception as e:
                    print(f"Failed to check container ports: {e}")
                
            time.sleep(1)
            
        raise TimeoutError(f"Container {self.container_name} failed to start within {timeout} seconds")
    def stop_container(self):
        stop_command = ["docker", "stop", self.container_name]
        result = subprocess.run(stop_command, capture_output=True, text=True)
        if result.returncode != 0:
            raise Exception(f"Failed to stop container: {result.stderr}")
    
    def run_command(self, command, stream_callback=None):
        """
        communicate with docker container and execute command, support stream output
        
        Args:
            command: the command to execute
            stream_callback: optional callback function, for handling stream output
                            the function signature should be callback(text: str)
        
        Returns:
            dict: the complete JSON result returned by the docker container
        """
        hostname = 'localhost'
        port = self.communication_port
        buffer_size = 4096
        # 优化 P1（2026-11-10）：长/静默命令曾在宿主机端 ~120s 触发 WinError 10060
        # （根因未最终定位，嫌疑 Windows TCP 空闲重置）。这里启用 TCP keepalive 主动
        # 保活以缓解空闲重置，并设一个宽松的可配接收空闲超时（默认 900s，env
        # COMMAND_RECV_TIMEOUT 可调，<=0 表示不设），避免连接永久挂死。
        # 注意：这是缓解措施而非根因修复；真实长命令仍建议容器内后台执行＋轮询日志。
        recv_timeout = float(os.getenv("COMMAND_RECV_TIMEOUT", "900"))

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            # Linux：细调 keepalive 空闲/间隔/次数（常量缺失则跳过）
            for opt, val in (("TCP_KEEPIDLE", 60), ("TCP_KEEPINTVL", 15), ("TCP_KEEPCNT", 8)):
                if hasattr(socket, opt):
                    try:
                        s.setsockopt(socket.IPPROTO_TCP, getattr(socket, opt), val)
                    except OSError:
                        pass
            # Windows：用 SIO_KEEPALIVE_VALS 设 keepalive（开启, 空闲 60s, 间隔 15s，单位 ms）
            if hasattr(s, "ioctl") and hasattr(socket, "SIO_KEEPALIVE_VALS"):
                try:
                    s.ioctl(socket.SIO_KEEPALIVE_VALS, (1, 60000, 15000))
                except OSError:
                    pass
            if recv_timeout > 0:
                s.settimeout(recv_timeout)
            s.connect((hostname, port))
            s.sendall(command.encode())
            # 修复（2026-11-10）：发完命令后半关闭写端，给服务端一个明确的
            # “命令结束”信号（EOF）。服务端 receive_all 相应改为读到 EOF 才停，
            # 不再用“recv 不足 4096 就算结束”的不可靠启发式——后者会把
            # create_file/write_file 的 ~100KB base64 命令从中间截断，bash 收到
            # 未闭合引号报 unexpected EOF，ML Agent 反复重写同一文件形成死循环。
            # 半关闭只关写端，下面仍能正常读取服务端响应。
            s.shutdown(socket.SHUT_WR)

            partial_line = ""
            # 增量解码器：避免多字节 UTF-8 字符被 recv 边界切断时 decode 抛异常
            decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
            while True:
                chunk = s.recv(buffer_size)
                if not chunk:
                    break

                # add new received data to the unfinished data
                data = partial_line + decoder.decode(chunk)
                lines = data.split('\n')
                
                # except the last line, process all complete lines
                for line in lines[:-1]:
                    if line:
                        try:
                            response = json.loads(line)
                            if response['type'] == 'chunk':
                                # process stream output
                                if stream_callback:
                                    stream_callback(response['data'])
                            elif response['type'] == 'final':
                                # return the final result
                                return {
                                    'status': response['status'],
                                    'result': response['result']
                                }
                        except json.JSONDecodeError:
                            print(f"Invalid JSON: {line}")
                
                # save the possibly unfinished last line
                partial_line = lines[-1]
                
        # if the loop ends normally without receiving a final response
        return {
            'status': -1,
            'result': 'Connection closed without final response'
        }
    
def with_env(env: DockerEnv):
    """将env注入到工具函数中的装饰器"""
    def decorator(func):
        def wrapped(*args, **kwargs):
            return func(env=env, *args, **kwargs)
        
        # 保留原始函数的所有属性
        update_wrapper(wrapped, func)
        # 修改signature，移除env参数
        wrapped.__signature__ = signature(func).replace(
            parameters=[p for p in signature(func).parameters.values() if p.name != 'env']
        )
        if func.__doc__:
            try:
                if '{docker_workplace}' in func.__doc__:
                    wrapped.__doc__ = func.__doc__.format(docker_workplace=env.docker_workplace)
                else:
                    wrapped.__doc__ = func.__doc__
                if '{local_workplace}' in func.__doc__:
                    wrapped.__doc__ = func.__doc__.format(local_workplace=env.local_workplace)
                else:
                    wrapped.__doc__ = func.__doc__
            except (KeyError, IndexError, ValueError):
                # 如果格式化失败（没有占位符），保持原始文档
                wrapped.__doc__ = func.__doc__
        return wrapped
    return decorator

def check_container_ports(container_name: str):
    """
    check if the container has port mapping
    return format:
    - if the container exists and has port mapping: '0.0.0.0:12345->12345/tcp'
    - if the container does not exist or does not have port mapping: None
    """
    # use docker ps to check the container and get the port information
    container_check_command = [
        "docker", "ps", "-a",
        "--filter", f"name={container_name}",
        "--format", "{{.Ports}}"
    ]
    
    result = subprocess.run(container_check_command, capture_output=True, text=True)
    ports_info = result.stdout.strip()
    
    if not ports_info:
        return None
        
    # only process the mapped ports
    for mapping in ports_info.split(','):
        mapping = mapping.strip()
        if '->' in mapping:
            # parse '0.0.0.0:12345->12345/tcp' to (12345, 12345)
            host_part, container_part = mapping.split('->')
            host_port = host_part.rsplit(':', 1)[1]  # support IPv4 and IPv6 bindings
            container_port = container_part.split('/')[0]  # get '12345' from '12345/tcp'
            if mapping.endswith('/tcp') and int(container_port) == 8000:
                return (int(host_port), int(container_port))
    return None

def check_container_exist(container_name: str):
    container_check_command = [
        "docker", "ps", "-a",
        "--filter", f"name={container_name}",
        "--format", "{{.Names}}"
    ]
    result = subprocess.run(container_check_command, capture_output=True, text=True)
    return container_name in result.stdout.strip()

def check_container_running(container_name: str):
    container_check_command = [
        "docker", "ps",
        "--filter", f"name={container_name}",
        "--format", "{{.Names}}"
    ]
    result = subprocess.run(container_check_command, capture_output=True, text=True)
    return container_name in result.stdout.strip()
