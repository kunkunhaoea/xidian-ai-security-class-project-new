import socket
import subprocess
import json
import argparse

parser = argparse.ArgumentParser()
parser.add_argument("--workplace", type=str, default=None)
parser.add_argument("--conda_path", type=str, default=None)
parser.add_argument("--port", type=int, default=None)
args = parser.parse_args()

if __name__ == "__main__":
    assert args.workplace is not None, "Workplace is not specified"
    assert args.conda_path is not None, "Conda path is not specified"
    assert args.port is not None, "Port is not specified"
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("0.0.0.0", args.port))
    server.listen(1)

    print(f"Listening on port {args.port}...")
    def receive_all(conn, buffer_size=65536):
        # 修复（2026-11-10）：读到真正的 EOF 才停，不再用“recv 不足 buffer_size
        # 就算结束”的启发式。TCP 是字节流，recv 在任何分段边界都可能返回不足
        # buffer_size 的数据（即使后面还有数据在途），原写法会把 create_file/
        # write_file 发送的 ~100KB base64 命令从中间截断，bash 报 unexpected EOF
        # 或写出损坏文件，ML Agent 反复重试同一写入。客户端 run_command 现在
        # 发完命令后 shutdown(SHUT_WR) 半关闭，EOF(b"") 才是可靠的命令结束标记。
        data = b""
        while True:
            part = conn.recv(buffer_size)
            if not part:
                break
            data += part
        return data.decode("utf-8", errors="replace")

    while True:
        conn, addr = server.accept()
        print(f"Connection from {addr}")
        while True:
            # command = conn.recv(1024).decode()
            command = receive_all(conn)
            if not command:
                break
            
            # Execute the command
            try:
                modified_command = f"/bin/bash -c 'source {args.conda_path}/etc/profile.d/conda.sh && conda activate autogpt && cd /{args.workplace} && {command}'"
                process = subprocess.Popen(modified_command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
                output = ''
                while True:
                    line = process.stdout.readline()
                    if not line and process.poll() is not None:
                        break
                    output += line
                    # 立即发送每一行输出
                    chunk_response = {
                        "type": "chunk",
                        "data": line
                    }
                    conn.send(json.dumps(chunk_response).encode() + b"\n")  # 添加换行符作为分隔符

                # 发送最终的完整响应
                final_response = {
                    "type": "final",
                    "status": process.poll(),
                    "result": output
                }
                conn.send(json.dumps(final_response).encode() + b"\n")
            except Exception as e:
                error_response = {
                    "type": "final",
                    "status": -1,
                    "result": f"Error running command: {str(e)}"
                }
                conn.send(json.dumps(error_response).encode() + b"\n")

            # Create a JSON response
            # response = {
            #     "status": exit_code,
            #     "result": output
            # }
            
            # # Send the JSON response
            # conn.send(json.dumps(response).encode())
        conn.close()