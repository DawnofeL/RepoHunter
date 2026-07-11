# RepoHunter 运行镜像。装好 python 依赖 + 两个系统命令（git 克隆用户要分析的仓库、
# ripgrep 供代码搜索工具用），拷进代码，起 uvicorn 服务。data/ 不进镜像，由 compose 挂卷。

FROM python:3.11-slim

# 运行时功能依赖：git 用来克隆被分析的 GitHub 仓库，ripgrep 给 grep_code/glob_files 工具用。
# 装完清掉 apt 缓存，别把这些留在镜像里占体积
RUN apt-get update && apt-get install -y --no-install-recommends \
        git ripgrep \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 先只拷 requirements 装依赖，代码改了不会让这层缓存失效，重建更快
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 再拷项目代码（.dockerignore 已排除 data、.git、notebook 等）
COPY . .

# 服务端口，跟 run.py 里的 PORT 一致
EXPOSE 8755

# 起服务。host 0.0.0.0 才能从容器外访问；用 run.py 顺带走它的容器检测（不唤浏览器）
CMD ["python", "run.py"]
