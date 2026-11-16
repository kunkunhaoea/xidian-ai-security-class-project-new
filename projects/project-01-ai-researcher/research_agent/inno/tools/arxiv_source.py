import urllib.parse
import feedparser
import time
import requests
import re
import tarfile
import os
from typing import List
def search_arxiv(query, max_results=10):
    """
    search arxiv papers
    
    Args:
        query (str): search keyword
        max_results (int): max return results
        
    Returns:
        list: list of papers info
    """
    search_arxiv.last_error = None  # 每次调用先清掉上一次的失败原因
    # 构建API URL
    # 修复（2026-11-04）：原来写的是 http，export.arxiv.org 现在对 http 返回 301，
    # 而 feedparser.parse(url) 不跟这个跳转，拿到的是空 feed，上层于是报
    # "Cannot find the paper '<title>' in arxiv"（实测一次 8 篇里 7 篇这样失败）。
    base_url = 'https://export.arxiv.org/api/query?'
    # 修复（2026-11-04）：原来先 urllib.parse.quote(query) 再把结果放进 params 交给
    # urlencode(params)，空格被转义两次（空格 -> %20 -> %2520），arXiv 收到字面 "%20"
    # 所以零命中；凡是多词标题都会返回空并让上层报
    # "Cannot find the paper '<title>' in arxiv"，论文 TeX 源码一步全部失效。
    # 这里只让 urlencode 转义一次，并用 ti:"..." 做短语精确匹配。
    search_query = f'ti:"{query}"'

    # 设置API参数
    params = {
        'search_query': search_query,
        'start': 0,
        'max_results': max_results,
        'sortBy': 'relevance',
        'sortOrder': 'descending'
    }
    
    # 构建完整的查询URL
    query_url = base_url + urllib.parse.urlencode(params)
    
    # 发送请求并解析结果
    # 修复（2026-11-04）：原来 feedparser.parse(query_url) 一次成型，没有超时也没有重试，
    # arXiv 限流或网络抖动时直接得到空结果，上层就当成“论文不存在”。
    # 现在用 requests 取回内容（带超时）再交给 feedparser，失败或零命中都退避重试。
    response = None
    last_error = "unknown"
    for attempt in range(1, 5):
        try:
            http_response = requests.get(query_url, timeout=(15, 60))
            if http_response.status_code == 200:
                parsed = feedparser.parse(http_response.content)
                if parsed.entries:
                    response = parsed
                    break
                # arXiv 限流时也可能返回 200 + 空结果，同样需要退避重试
                last_error = "HTTP 200 but 0 entries (likely rate limited)"
                time.sleep(10 * attempt)
                continue
            if http_response.status_code in (429, 500, 502, 503, 504):
                last_error = f"HTTP {http_response.status_code} (rate limited)"
                time.sleep(10 * attempt)
                continue
            last_error = f"HTTP {http_response.status_code}"
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            time.sleep(5 * attempt)
    if response is None:
        print(f"[search_arxiv] giving up on {query!r}: {last_error}")
        # 把失败原因带出去，避免上层一律说成“论文不存在”——限流和真的没有是两回事
        search_arxiv.last_error = last_error
        return []
    
    # 提取论文信息
    papers = []
    for entry in response.entries:
        paper = {
            'title': entry.title,
            'author': [author.name for author in entry.authors],
            'published': entry.published,
            'summary': entry.summary,
            'url': entry.link,
            'pdf_url': next(link.href for link in entry.links if link.type == 'application/pdf')
        }
        papers.append(paper)
        
        # 遵守API速率限制
        time.sleep(0.5)
    
    return papers

def extract_tex_content(tar_path, ):
    """
    从tar.gz文件中提取所有.tex文件的内容
    
    参数:
        tar_path: tar.gz文件路径
    
    返回:
        str: 所有.tex文件内容的拼接，格式为 filename + content
    """
    try:
        all_content = []
        
        with tarfile.open(tar_path, 'r:gz') as tar:
            # 获取所有.tex文件
            tex_files = [f for f in tar.getmembers() if f.name.endswith('.tex')]
            
            for tex_file in tex_files:
                # 提取文件内容
                f = tar.extractfile(tex_file)
                if f is not None:
                    try:
                        # 尝试以utf-8解码
                        content = f.read().decode('utf-8')
                    except UnicodeDecodeError:
                        # 如果utf-8失败，尝试latin-1
                        f.seek(0)
                        content = f.read().decode('latin-1')
                    
                    # 添加文件名和内容
                    all_content.append(f"\n{'='*50}\nFilename: {tex_file.name}\n{'='*50}\n")
                    all_content.append(content)
                    all_content.append("\n\n")
        
        # 将所有内容拼接成一个字符串
        return "".join(all_content)
    
    except Exception as e:
        return f"Extract failed with error: {str(e)}"

def _safe_slug(title: str) -> str:
    """把论文标题变成跨平台合法的文件名。

    原实现只做 `title.replace(' ', '_').lower()`，标题里的冒号（例如
    "Finite scalar quantization: VQ-VAE made simple"）在 Windows 上是非法文件名字符，
    open() 直接抛 [Errno 22]，整篇论文的证据就丢了。
    """
    slug = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '', title)
    slug = re.sub(r'\s+', '_', slug).strip('._').lower()  # Windows 不允许文件名以 . 结尾
    return slug[:120].strip('._') or "paper"


def download_arxiv_source(arxiv_url, local_root, workplace_name, title: str):
    """
    download arxiv paper source file

    Args:
        arxiv_url: arxiv paper url, e.g. 'http://arxiv.org/abs/2006.11239v2'
        local_root: local root directory
        workplace_name: workplace name
    """
    # 修复（2026-11-04）：原实现有四个会让“论文证据”整条腿失效的问题，
    # 实测 8 篇参考文献只成功下到 2 篇：
    #   1) requests.get 无超时、无重试 -> IncompleteRead / 连接中断即整篇失败；
    #   2) 只用 http://arxiv.org/src/<id> 一个源，失败后没有退路；
    #   3) 写 .tex 用平台默认编码（中文 Windows 是 cp936），后续按 utf-8 读时报
    #      'utf-8' codec can't decode byte 0xa1，代理拿不到正文只会反复重试；
    #   4) arXiv 有时返回单个 .tex 而不是 tar.gz，extract 失败就当成下载失败。
    # 现在：多源 + 超时 + 退避重试 + 流式写盘 + utf-8 落盘 + 单文件 tex 兜底。
    slug = _safe_slug(title)
    src_dir = os.path.join(local_root, workplace_name, "paper_source")
    tex_dir = os.path.join(local_root, workplace_name, "papers")
    tar_path = os.path.join(src_dir, f"{slug}.tar.gz")
    tex_path = os.path.join(tex_dir, f"{slug}.tex")

    try:
        paper_id = re.search(r'abs/([^/]+)', arxiv_url).group(1)
    except Exception:
        return {"status": -1, "message": f"Download paper '{title}' failed: cannot parse an arXiv id from {arxiv_url}", "path": None}

    try:
        os.makedirs(src_dir, exist_ok=True)
        os.makedirs(tex_dir, exist_ok=True)
    except Exception as e:
        return {"status": -1, "message": f"Download paper '{title}' failed to create directories: {e}", "path": None}

    last_error = None
    downloaded = False
    for source_url in (f"https://arxiv.org/src/{paper_id}", f"https://export.arxiv.org/e-print/{paper_id}"):
        for attempt in range(1, 4):
            try:
                with requests.get(source_url, timeout=(15, 180), stream=True) as response:
                    if response.status_code in (429, 500, 502, 503, 504):
                        # arXiv 对密集请求限流：实测连续下 8 篇时大面积返回 429。
                        # 这类错误退避之后重试同一个源是有意义的，不能直接放弃。
                        last_error = f"HTTP {response.status_code} (rate limited) from {source_url}"
                        time.sleep(10 * attempt)
                        continue
                    if response.status_code != 200:
                        last_error = f"HTTP {response.status_code} from {source_url}"
                        break  # 404/403 之类重试没意义，换下一个源
                    with open(tar_path, "wb") as fh:
                        for chunk in response.iter_content(chunk_size=1 << 20):
                            if chunk:
                                fh.write(chunk)
                downloaded = True
                break
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc} from {source_url} (attempt {attempt}/3)"
                time.sleep(5 * attempt)
        if downloaded:
            break
        time.sleep(3)  # 换源之前也缓一下，避免继续触发限流

    if not downloaded:
        return {"status": -1, "message": f"Download paper '{title}' failed: {last_error}", "path": None}

    tex_content = ""
    try:
        tex_content = extract_tex_content(tar_path) or ""
    except Exception as exc:
        last_error = f"extract failed: {type(exc).__name__}: {exc}"
    if tex_content.startswith("Extract failed") or not tex_content.strip():
        # arXiv 对部分论文直接返回单个 tex 文件而不是 tar.gz
        try:
            with open(tar_path, "r", encoding="utf-8", errors="replace") as fh:
                raw = fh.read()
            if "\\document" in raw or "\\begin{document}" in raw:
                tex_content = raw
        except Exception:
            pass
    if not tex_content.strip():
        size = os.path.getsize(tar_path) if os.path.exists(tar_path) else 0
        return {
            "status": -1,
            "message": (
                f"Download paper '{title}' failed: got {size} bytes from arXiv but no TeX content "
                f"could be extracted ({last_error}). The source may be PDF-only."
            ),
            "path": None,
        }

    try:
        with open(tex_path, "w", encoding="utf-8", errors="replace") as fh:
            fh.write(tex_content)
    except Exception as e:
        return {"status": -1, "message": f"Download paper '{title}' failed while writing {tex_path}: {e}", "path": None}

    return {
        "status": 0,
        "message": f"Download paper '{title}' successfully ({len(tex_content)} chars of TeX)",
        "path": f"/{workplace_name}/papers/{slug}.tex",
    }

def download_arxiv_source_by_title(paper_list: List[str], local_root: str, workplace_name: str):
    """
    download arxiv paper source file by title
    
    Args:
        title: paper title
        paper_dir: paper directory
    """
    ret_msg = []
    # 修复（2026-11-04）：原来 8 篇论文背靠背连续请求 arXiv，实测大面积 429 限流。
    # arXiv 官方建议请求间隔不少于 3 秒，这里在每篇之间加礼貌延时，并打印进度，
    # 免得看起来像卡死。
    for idx, title in enumerate(paper_list):
        if idx:
            time.sleep(3)
        print(f"[download_arxiv_source_by_title] {idx + 1}/{len(paper_list)} {title[:60]}", flush=True)
        papers = search_arxiv(title, max_results=1)
        if len(papers) == 0:
            # 修复（2026-11-04）：原来一律回 "Cannot find the paper ... in arxiv"，
            # 但实测多数情况是 arXiv 限流（429 / 200+空结果），不是论文不存在。
            # 这句错误信息会误导模型：它以为换个说法再搜就能找到，于是反复重试。
            reason = getattr(search_arxiv, "last_error", "") or "no results"
            ret_msg.append(
                f"Could not retrieve '{title}' from arXiv ({reason}). This is usually arXiv "
                "rate limiting rather than the paper not existing. Do not retry this download; "
                "continue with the papers that were retrieved successfully."
            )
            continue
        paper = papers[0]
        download_info =  download_arxiv_source(paper['url'], local_root, workplace_name, title)
        if download_info["status"] == -1:
            ret_msg.append(download_info["message"])
        else:
            ret_msg.append(download_info["message"] + f"\nThe paper is downloaded to path: {download_info['path']}")
    ok = sum(1 for m in ret_msg if "successfully" in m)
    print(f"[download_arxiv_source_by_title] done: {ok}/{len(paper_list)} papers downloaded", flush=True)
    return "\n".join(ret_msg)
    