import streamlit as st
st.title("Yuki 1.5")
st.subheader("Make Learning easier and automatic,Being an independent learner")

with st.sidebar:
    st.markdown("## 技术拆解")
    st.markdown('''### 第一步: 材料加工
    #### 工具:Paddle OCR, PDF文件处理，包括拆分
    #### 输入:PDF文件，图片，word等等,输出:markdown文件
    ''')
    st.divider()
    st.markdown('### 第二步: 材料处理')
    st.markdown('#### 工具:Hermes Agent')
    st.markdown('''#### 任务1:多个文件重复性知识点筛选,重点知识筛选,知识点挑选,适用于医学知识学习
    ##### 三个知识库:重点知识库,零碎知识库,习题知识库
    ##### 工作流:文件内知识点提取->全部进入零碎知识库->根据重要性(重复度判定,习题知识库提取知识点)分配权重->高权重知识点进入重点知识库
    低权重保留在零碎知识库
    ##### 技术使用 Langchain.prompts提示词
    ##### 任务目的:70分原则,应付考试
    ''')
    st.markdown('''#### 任务2:其它知识学习
    ##### 学习方式:对话式学习
    ##### 学习细节:概念+示例,可选择对话记入笔记
    ''')
    st.divider()
    st.markdown('### 第三步:笔记生成')
    st.markdown('##### 工具: Hermes Agent+Obsidian')
    st.markdown('''##### 对于任务一,结合三个知识库,生成每个章节的重点知识点,名词解释和简答题,最后生成考前复习重点，生成Obsidian笔记，遵循70分原则
    ##### 工作流:输入课本markdown,作为内容索引知识库,并按照既定章节,重点知识库,习题知识库生成重点知识点,零碎知识库作为知识补充,名词解释和简答题根据习题知识库生成
    ##### 学习细节:Anki卡片,快速记忆,口诀,对话式学习,并将前一天的学习内容进行总结,生成复习笔记,方便复习,复习笔记按照时间划分
    ##### 技术实现:Langchain
    ##### 快速学习,不易遗忘''')
    st.markdown('##### 对于任务二,对话式学习,并创建notes供我补充做笔记,有项目实战区供我写代码进行知识巩固')



# 定义模式页面
page_medicine = st.Page('medicine_learning.py',title="医学学习",icon = "👨‍⚕️")
page_computer = st.Page('computer_learning.py',title = "计算机知识学习",icon = "💻")

mode = st.selectbox(label = '学习模式',options = ['医学学习','计算机知识学习','其它'])

#选中的页面会传给st.navigation[citation]
if mode == "医学学习":
    Pg = st.navigation([page_medicine])
elif mode == "计算机知识学习":
    Pg = st.navigation([page_computer])

Pg.run()



