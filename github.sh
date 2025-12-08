#!/bin/bash

# GitHub文件自动下载脚本
# 支持可选的GitHub Token以提高API限制
# 支持为每个文件/目录单独指定下载位置

# 默认配置
DEFAULT_DOWNLOAD_DIR="$HOME/rss"

# 尝试从.env文件加载GitHub Token（可选）
if [[ -f "$(dirname "$0")/.env" ]]; then
    source "$(dirname "$0")/.env"
fi

# 要监控的GitHub文件/目录列表 - 格式：
# "URL|目标目录"
# 系统会自动根据URL判断是文件还是目录
DOWNLOAD_TARGETS=(
    # 单个文件示例rss
     "https://raw.githubusercontent.com/penggan00/rss/main/rss.py|$HOME/rss"
     "https://raw.githubusercontent.com/penggan00/rss/main/rss_config.py|$HOME/rss"
     "https://raw.githubusercontent.com/penggan00/rss/main/gpt.py|$HOME/rss"
     "https://raw.githubusercontent.com/penggan00/rss/main/qq.py|$HOME/rss"
     "https://raw.githubusercontent.com/penggan00/rss/main/mail.py|$HOME/rss"
#blog
     "https://raw.githubusercontent.com/penggan00/penggan00.github.io/main/index.html|$HOME/myblog/blog"
    # GitHub整个目录下载
   # "https://api.github.com/repos/penggan00/rss/contents/|$HOME/rss"
   # "https://api.github.com/repos/penggan00/penggan00.github.io/contents/|$HOME/myblog/blog"
    "https://api.github.com/repos/penggan00/penggan00.github.io/contents/linux|$HOME/myblog/blog/linux"
    "https://api.github.com/repos/penggan00/penggan00.github.io/contents/my-blog|$HOME/myblog/blog/my-blog"
    "https://api.github.com/repos/penggan00/penggan00.github.io/contents/static|$HOME/myblog/blog/static"
    # "https://api.github.com/repos/penggan00/penggan00.github.io/contents/tv|$HOME/myblog/blog/tv"

    # "https://api.github.com/repos/penggan00/rss/contents/configs|$HOME/rss/configs"
    # "https://api.github.com/repos/penggan00/rss/contents/scripts|$HOME/rss/scripts"
    # "https://api.github.com/repos/penggan00/rss/contents/data|$HOME/rss/data"
)

# 显示使用说明
show_usage() {
    echo "用法: $0 [选项]"
    echo ""
    echo "选项:"
    echo "  -s, --status            显示文件状态"
    echo "  -h, --help             显示此帮助信息"
    echo ""
    echo "环境变量:"
    echo "  GITHUB_TOKEN            GitHub Token（用于提高API限制）"
    echo ""
    echo "示例:"
    echo "  $0                      # 更新所有文件和目录"
    echo "  $0 --status             # 显示所有文件和目录的状态"
    echo ""
    echo "注意: 系统会自动根据URL判断是文件还是目录"
}

# 解析URL和目标目录
parse_file_entry() {
    local entry="$1"
    local url="${entry%|*}"
    local dir="${entry#*|}"
    echo "$url" "$dir"
}

# 从URL获取文件名
get_filename_from_url() {
    local url="$1"
    # 对于 raw.githubusercontent.com URL
    if [[ $url =~ https://raw.githubusercontent.com/[^/]+/[^/]+/[^/]+/(.+) ]]; then
        basename "${BASH_REMATCH[1]}"
    # 对于API URL，我们无法从URL中获取文件名，需要从API响应中获取
    else
        # 对于目录，返回一个默认名称
        if [[ $url == https://api.github.com/repos/*/contents/* ]]; then
            if [[ $url =~ https://api.github.com/repos/[^/]+/[^/]+/contents/(.+) ]]; then
                local path="${BASH_REMATCH[1]}"
                if [[ -z "$path" ]] || [[ "$path" == *"?"* ]]; then
                    echo "github_directory"
                else
                    basename "$path"
                fi
            else
                echo "github_directory"
            fi
        else
            basename "$url"
        fi
    fi
}

# 判断URL是否为目录
is_directory_url() {
    local url="$1"
    # API URLs are directories, raw URLs are files
    if [[ $url == https://api.github.com/repos/*/contents* ]]; then
        return 0  # true, is directory
    else
        return 1  # false, is file
    fi
}

# 检查依赖
check_dependencies() {
    local deps=("curl" "jq")
    local missing_deps=()
    
    for dep in "${deps[@]}"; do
        if ! command -v "$dep" &> /dev/null; then
            missing_deps+=("$dep")
        fi
    done
    
    if [ ${#missing_deps[@]} -gt 0 ]; then
        echo "安装缺失的依赖: ${missing_deps[*]}"
        
        if command -v apt-get &> /dev/null; then
            sudo apt-get update
            sudo apt-get install -y "${missing_deps[@]}"
        elif command -v yum &> /dev/null; then
            sudo yum install -y "${missing_deps[@]}"
        elif command -v dnf &> /dev/null; then
            sudo dnf install -y "${missing_deps[@]}"
        elif command -v pacman &> /dev/null; then
            sudo pacman -Sy --noconfirm "${missing_deps[@]}"
        elif command -v zypper &> /dev/null; then
            sudo zypper install -y "${missing_deps[@]}"
        else
            echo "错误: 无法自动安装依赖，请手动安装: ${missing_deps[@]}"
            exit 1
        fi
    fi
}

# 确保下载目录存在
ensure_download_dir() {
    local dir="$1"
    if [ ! -d "$dir" ]; then
        echo "创建目录: $dir"
        mkdir -p "$dir"
        if [ $? -ne 0 ]; then
            echo "错误: 无法创建目录 $dir"
            return 1
        fi
    fi
    return 0
}

# 获取本地文件的Git SHA1哈希
get_local_git_sha() {
    local file_path="$1"
    if [[ -f "$file_path" ]]; then
        local file_size
        if stat -c%s "$file_path" &>/dev/null; then
            file_size=$(stat -c%s "$file_path")
        else
            file_size=$(stat -f%z "$file_path")
        fi
        (printf "blob %d\0" "$file_size"; cat "$file_path") | sha1sum | cut -d' ' -f1
    else
        echo ""
    fi
}

# 从GitHub API获取文件SHA
get_github_sha() {
    local raw_url="$1"
    local is_api_url="$2"
    
    if [[ -n "$is_api_url" ]]; then
        # API URL
        if [[ -n "$GITHUB_TOKEN" ]]; then
            curl -s -H "Authorization: token $GITHUB_TOKEN" "$raw_url" | jq -r '.sha // ""'
        else
            curl -s "$raw_url" | jq -r '.sha // ""'
        fi
    else
        # raw.githubusercontent.com URL
        if [[ $raw_url =~ https://raw.githubusercontent.com/([^/]+)/([^/]+)/([^/]+)/(.+) ]]; then
            local user="${BASH_REMATCH[1]}"
            local repo="${BASH_REMATCH[2]}"
            local branch="${BASH_REMATCH[3]}"
            local path="${BASH_REMATCH[4]}"
            
            local api_url="https://api.github.com/repos/$user/$repo/contents/$path?ref=$branch"
            
            if [[ -n "$GITHUB_TOKEN" ]]; then
                curl -s -H "Authorization: token $GITHUB_TOKEN" "$api_url" | jq -r '.sha // ""'
            else
                curl -s "$api_url" | jq -r '.sha // ""'
            fi
        else
            echo ""
        fi
    fi
}

# 下载单个文件
download_single_file() {
    local filename="$1"
    local url="$2"
    local target_dir="$3"
    local temp_file="$target_dir/$filename.tmp"
    local final_file="$target_dir/$filename"
    
    echo "下载文件: $filename 到目录: $target_dir"
    
    if curl -s -o "$temp_file" "$url"; then
        mv "$temp_file" "$final_file"
        echo "完成: $filename"
        return 0
    else
        rm -f "$temp_file"
        echo "失败: $filename"
        return 1
    fi
}

# 下载GitHub目录中的所有文件
download_github_directory() {
    local api_url="$1"
    local target_dir="$2"
    local item_name="$3"
    
    echo "开始下载: $item_name 到 $target_dir"
    
    # 获取目录内容
    local api_response
    if [[ -n "$GITHUB_TOKEN" ]]; then
        api_response=$(curl -s -H "Authorization: token $GITHUB_TOKEN" "$api_url")
    else
        api_response=$(curl -s "$api_url")
    fi
    
    # 检查是否获取成功
    if [[ $(echo "$api_response" | jq -r 'type') != "array" ]]; then
        echo "错误: 无法获取目录内容 $item_name (URL: $api_url)"
        return 1
    fi
    
    local file_count=0
    local success_count=0
    
    # 遍历目录中的每个项目
    echo "$api_response" | jq -c '.[]' | while read -r item; do
        local item_type=$(echo "$item" | jq -r '.type')
        local item_filename=$(echo "$item" | jq -r '.name')
        local item_path=$(echo "$item" | jq -r '.path')
        local download_url=$(echo "$item" | jq -r '.download_url // ""')
        local sha=$(echo "$item" | jq -r '.sha')
        
        ((file_count++))
        
        if [[ "$item_type" == "file" && -n "$download_url" ]]; then
            # 文件下载
            local local_file="$target_dir/$item_filename"
            
            # 确保目标目录存在
            ensure_download_dir "$(dirname "$local_file")"
            
            # 获取本地文件的Git SHA1哈希
            local local_sha=$(get_local_git_sha "$local_file")
            
            if [[ -z "$local_sha" ]] || [[ "$local_sha" != "$sha" ]]; then
                echo "  > 下载: $item_filename"
                if curl -s -o "$local_file" "$download_url"; then
                    ((success_count++))
                else
                    echo "  ! 失败: $item_filename"
                fi
            else
                echo "  = 最新: $item_filename"
                ((success_count++))
            fi
            
        elif [[ "$item_type" == "dir" ]]; then
            # 递归下载子目录
            echo "  + 进入子目录: $item_filename"
            
            # 对于子目录，需要在target_dir下创建对应的子目录
            local subdir_path="$target_dir/$item_filename"
            ensure_download_dir "$subdir_path"
            
            # 构建正确的子目录API URL
            local repo_base_url="https://api.github.com/repos"
            
            # 从原始URL中提取仓库信息
            if [[ "$api_url" =~ https://api.github.com/repos/([^/]+/[^/]+)/contents(.*) ]]; then
                local repo="${BASH_REMATCH[1]}"
                local current_path="${BASH_REMATCH[2]}"
                
                # 构建新的路径
                local new_path
                if [[ -z "$current_path" ]] || [[ "$current_path" == "/" ]]; then
                    new_path="/$item_filename"
                else
                    new_path="$current_path/$item_filename"
                fi
                
                # 构建完整的API URL
                local subdir_api_url="$repo_base_url/$repo/contents$new_path"
                
                # 保留查询参数（如果有）
                if [[ "$api_url" == *"?"* ]]; then
                    local query_params="${api_url#*\?}"
                    subdir_api_url="$subdir_api_url?$query_params"
                fi
                
                # 递归调用
                download_github_directory "$subdir_api_url" "$subdir_path" "$item_filename"
            else
                echo "  ! 无法解析仓库URL: $api_url"
            fi
        fi
    done
    
    echo "目录 $item_name 下载完成: $success_count/$file_count 个文件"
    return 0
}

# 主检查函数
check_and_update_files() {
    echo "检查文件和目录更新..."
    
    # 显示当前使用的认证方式
    if [[ -n "$GITHUB_TOKEN" ]]; then
        echo "使用GitHub Token认证"
    else
        echo "使用匿名访问"
    fi
    
    local updated_count=0
    local total_count=0
    
    for entry in "${DOWNLOAD_TARGETS[@]}"; do
        read -r url target_dir <<< "$(parse_file_entry "$entry")"
        
        ((total_count++))
        
        # 确保目标目录存在
        if ! ensure_download_dir "$target_dir"; then
            echo "跳过项目 (目录创建失败)"
            continue
        fi
        
        # 判断是文件还是目录
        if is_directory_url "$url"; then
            # 目录下载
            echo ""
            # 从URL获取一个描述性的名字
            local item_name=$(get_filename_from_url "$url")
            echo "处理目录: $item_name"
            echo "目标目录: $target_dir"
            
            download_github_directory "$url" "$target_dir" "$item_name"
            ((updated_count++))
        else
            # 单个文件下载
            local filename=$(get_filename_from_url "$url")
            local local_file="$target_dir/$filename"
            
            # 获取GitHub文件SHA
            local github_sha
            if [[ "$url" == https://api.github.com/* ]]; then
                github_sha=$(get_github_sha "$url" "api")
            else
                github_sha=$(get_github_sha "$url")
            fi
            
            if [[ -z "$github_sha" ]]; then
                echo "错误: 无法获取 $filename 的GitHub SHA"
                continue
            fi
            
            # 获取本地文件的Git SHA1哈希
            local local_sha=$(get_local_git_sha "$local_file")
            
            if [[ -z "$local_sha" ]]; then
                # 本地文件不存在，下载文件
                echo "下载新文件: $filename 到 $target_dir"
                if download_single_file "$filename" "$url" "$target_dir"; then
                    ((updated_count++))
                fi
            elif [[ "$local_sha" != "$github_sha" ]]; then
                # 哈希不匹配，需要更新
                echo "更新文件: $filename 在 $target_dir"
                if download_single_file "$filename" "$url" "$target_dir"; then
                    ((updated_count++))
                fi
            else
                # 文件已是最新
                echo "文件最新: $filename (位置: $target_dir)"
            fi
        fi
    done
    
    echo ""
    if [ $updated_count -eq 0 ]; then
        echo "所有项目都是最新版本"
    else
        echo "更新完成: 处理了 $total_count 个项目"
    fi
}

# 显示文件状态
show_status() {
    echo "文件状态:"
    
    if [[ -n "$GITHUB_TOKEN" ]]; then
        echo "认证: 使用GitHub Token"
    else
        echo "认证: 匿名访问"
    fi
    echo ""
    
    local index=0
    for entry in "${DOWNLOAD_TARGETS[@]}"; do
        read -r url target_dir <<< "$(parse_file_entry "$entry")"
        
        ((index++))
        
        if is_directory_url "$url"; then
            # 目录的状态
            local item_name=$(get_filename_from_url "$url")
            echo "项目 $index: $item_name"
            echo "  类型: 目录"
            echo "  目标目录: $target_dir"
            echo "  URL: $url"
            
            if [[ -d "$target_dir" ]]; then
                local file_count=$(find "$target_dir" -type f | wc -l)
                echo "  状态: 已下载"
                echo "  文件数量: $file_count"
            else
                echo "  状态: 未下载"
            fi
        else
            # 单个文件的状态
            local filename=$(get_filename_from_url "$url")
            local local_file="$target_dir/$filename"
            
            echo "项目 $index: $filename"
            echo "  类型: 文件"
            echo "  目标目录: $target_dir"
            echo "  URL: $url"
            
            if [[ -f "$local_file" ]]; then
                local local_sha=$(get_local_git_sha "$local_file")
                local github_sha
                
                if [[ "$url" == https://api.github.com/* ]]; then
                    github_sha=$(get_github_sha "$url" "api")
                else
                    github_sha=$(get_github_sha "$url")
                fi
                
                if [[ -n "$local_sha" && -n "$github_sha" ]]; then
                    if [[ "$local_sha" == "$github_sha" ]]; then
                        echo "  状态: 最新"
                    else
                        echo "  状态: 需要更新"
                    fi
                else
                    echo "  状态: 无法验证"
                fi
            else
                echo "  状态: 未下载"
            fi
        fi
        echo ""
    done
}

# 主程序
main() {
    # 检查依赖
    check_dependencies
    
    case "${1:-}" in
        -s|--status)
            show_status
            ;;
        -h|--help)
            show_usage
            ;;
        *)
            check_and_update_files
            ;;
    esac
}

main "$@"