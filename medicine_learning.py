import streamlit as st

st.title("Yuki 1.5-医学学习")
st.divider()
st.markdown("## Step1-文件处理")
column1,column2 = st.columns([1,1])
with column1:
    st.markdown("#### PDF文件拆分")
    files = st.file_uploader(label = "上传资料",type = ['pdf','word','txt','ppt'])
    st.text('请选择页码')
    st.text_input("")

with column2:
    st.markdown('#### 图片处理')
    pictures = st.file_uploader(label = "上传图片",type = ['png','jpg','jpeg'])






