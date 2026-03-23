# Eolink Open API 常见接口路径参考

本文档记录不同 Eolink 版本/部署方式下的 Open API 路径差异，供 skill 在遭遇 404 时参考。

## Eolink Apikit 云版（SaaS）

基础地址：`https://apis.eolink.com/api`

| 功能 | 方法 | 路径 |
|------|------|------|
| 获取环境列表 | POST | `/v2/api_studio/management/project/get_customizeList` |
| 获取项目列表 | POST | `/v2/api_studio/management/project/search` |
| 获取API测试用例列表 | GET | `/v2/api_studio/management/test_case/get_list` |
| 获取测试用例详情 | GET | `/v2/api_studio/management/test_case/get_info` |
| 新增API测试用例 | POST | `/v2/api_studio/management/test_case/add` |
| 编辑API测试用例接口 | POST | `/v2/api_studio/management/test_case/edit` |
| 批量删除API测试用例 | POST | `/v2/api_studio/management/test_case/delete` |
| 获取/查询 API | POST | `/v2/api_beacon/api/basices` |
| 获取接口字段/参数详情 | GET | `/v2/api_studio/management/api/get_api` |
| 执行指定API的所有测试用例并获取报告 | POST | `/v2/api_studio/management/api_test_case/execute` |



认证方式：请求 Header 中加入 `Eo-Secret-Key: {API_KEY}`

## Eolink 私有部署版（On-Premise）

基础地址视部署情况而定，路径前缀通常为 `/api/` 或 `/openapi/`。

常见差异：
- 部分版本使用 `Authorization: Bearer {token}` 代替 `EEo-Secret-Key`
- 部分版本路径为 `/openapi/v1/project/apis`
- 如遇问题请让用户在 Eolink 控制台 → 账户设置 → Open API 页面查看实际的 curl 示例

## 获取 API Key 的方式

1. 登录 Eolink 平台
2. 点击右上角头像 → **账户设置**
3. 找到 **Open API** 或 **API Key** 选项
4. 创建并复制 API Key

## 获取 projectHashKey 的方式

1. 进入目标项目 
2. 查看浏览器 URL，形如 `https://space-o74jhl.w.eolink.com/`
3. 其中 `space-o74jhl` 即为 `space_id`
4. 也可在项目设置页面查看
