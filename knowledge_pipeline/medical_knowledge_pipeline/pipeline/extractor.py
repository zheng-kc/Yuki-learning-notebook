"""知识点提取模块(①)。

职责:
    遍历 subject/<科目>/<材料类型>/ 下的 md 文件,逐文件调用 LLM,
    按提示词模板提取结构化知识点,返回统一格式供 kb_store 入库。

规划接口(待实现):
    extract_from_md(md_path, material_type, prompt_path) -> list[dict]
        输入:单份 md 文件路径、材料类型(课本/PPT/习题册/复习资料)、提示词路径
        输出:知识点列表,每项含 content/chapter/source_file 等字段

    extract_subject(subject, material_types=None) -> dict
        输入:科目名,可选材料类型过滤
        输出:{material_type: [知识点...]}

设计要点:
    - 逐文件调用(独立上下文),避免上下文溢出 —— 用户既定决策
    - 调用方式待定:hermes CLI 子进程 / 直接 API(见 config.LLM_MODE)
    - 输出 JSON schema 校验,失败重试
    - 幂等:已处理文件记录,重复跑跳过
"""
