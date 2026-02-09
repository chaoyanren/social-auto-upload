import os
from datetime import datetime

def rename_videos_and_create_txt(folder_path="videos"):
    # 获取videos文件夹中所有.mp4文件
    files = [f for f in os.listdir(folder_path) if f.lower().endswith(".mp4")]
    
    # 按当前时间_数字进行重命名
    # 时间格式示例：20250209_153045（年月日_时分秒）
    current_time = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    for i, video_file in enumerate(files, start=1):
        old_path = os.path.join(folder_path, video_file)
        
        # 新的文件名：20250209_153045_1.mp4
        new_filename = f"{current_time}_{i}.mp4"
        new_path = os.path.join(folder_path, new_filename)
        
        # 重命名 .mp4 文件
        os.rename(old_path, new_path)
        
        # 创建对应的 .txt 文件，并写入指定内容
        txt_filename = f"{current_time}_{i}.txt"
        txt_path = os.path.join(folder_path, txt_filename)
        
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write("AI生成\n#AI生成")

if __name__ == "__main__":
    # 使用默认videos文件夹，可在此处自定义路径
    rename_videos_and_create_txt("videos")
