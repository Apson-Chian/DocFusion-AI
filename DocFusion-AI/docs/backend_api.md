# Backend API

## 1 上传文件

POST /upload

form-data:
file: 文件

返回：
{
  task_id: int
}

## 2 查询任务

GET /tasks/{task_id}

返回：
{
  task_id: int
  status: string
  result: object
}