# 百里才教育 · 全能小达人习惯营（诚实合规版）

一套「家长端 H5 打卡 + 文字考勤 + 自愿照片荣誉墙 + 后台管理」的完整项目。
核心原则：**开关说真话**——家长关闭开关时照片只在本机合成、绝不上传；开启后才上传照片到私有服务器并展示在荣誉墙，老师可随时下架。

## 目录
```
bailicai/
├── app.py                 # Flask 后端
├── checkin.html           # 家长端 H5（已内嵌 logo）
├── requirements.txt
├── templates/
│   ├── admin_login.html   # 后台登录
│   ├── admin.html         # 后台总览（考勤表 / 下架 / 删除）
│   └── wall.html          # 公共荣誉墙
└── data/                  # 运行时生成：checkin.db、uploads/（照片）
```

## 本地运行
```bash
cd bailicai
pip install -r requirements.txt
export BAILICAI_ADMIN_PASSWORD="换成你的强密码"
export BAILICAI_SECRET="一串随机字符串"
python app.py
```
- 家长端测试：http://localhost:5000/
- 后台：http://localhost:5000/admin
- 荣誉墙：http://localhost:5000/wall

## 让 H5 连上后端
打开 `checkin.html`，把脚本顶部的 `API_BASE` 改成你的服务地址：
```js
const API_BASE = "https://api.你的域名.com";
```
- 留空 `""` 时为**纯本地模式**：不上传任何数据，告知卡与开关自动隐藏（适合先单独发 H5）。
- 填入地址后：文字考勤随「生成成就卡」上报；照片仅在家长开启开关时上传。

## 数据是怎么存的（数据分离 + 阶段分档）
- **文字考勤**（姓名、第几天、今天打卡的内容文本）：默认入库，供老师在后台查看。
  - 若希望「一切都需家长主动同意」，把 `app.py` 里 `ALWAYS_RECORD_ATTENDANCE` 改为 `False`，未公开的记录将完全不入库。
- **阶段分档**：家长在 H5 选「学龄前 / 小学生」。
  - **学龄前（幼儿）**：默认只在本机留念，**前端锁定 + 服务端强制**——照片绝不上传、绝不公开（即使有人篡改前端强行提交，后端也会拦成不公开、不存照片）。
  - **小学生**：才允许家长自愿开启开关、上传照片到荣誉墙。
- **照片**：仅「小学档 + 家长开启开关 + 有照片」时才保存；服务端用 Pillow 重新编码并缩放，**自动抹除原图 EXIF/GPS 元数据**，随机文件名存于 `data/uploads/`（不在 web 静态目录）。公开访问 `/media/<文件>` 会校验「已同意且未下架」。
- **自定义任务**：前后端都会清洗（去链接、去特殊字符、限长 18 字）并过滤敏感词（`BAD_WORDS`，可扩充），避免奇怪内容被合成进带 logo 的成就卡。

## 上线前务必做（交给机构合规老师核对）
1. **强密码**：通过环境变量设置 `BAILICAI_ADMIN_PASSWORD`，不要用默认值。
2. **HTTPS**：用 Nginx + 证书，全站走 https；生产用 `gunicorn -w 2 app:app` 而非 `python app.py`。
3. **监护人同意**：报名时即书面告知并取得监护人**单独同意**——明确说明「完成情况会记录给老师」「开启开关后照片会上传并在荣誉墙展示」。不满 14 周岁未成年人信息属敏感个人信息。
4. **私有存储**：照片留在机构自己的私有服务器，**不要**用公开图床。
5. **保留期与删除**：设定保留天数（`RETENTION_DAYS`，默认 60），到期用 `/api/admin/cleanup` 或定时任务清理；并明确「家长申请删除孩子信息」的处理流程（后台「删除」按钮即可彻底删除记录与照片）。
6. **荣誉墙展示范围**：默认展示家长填写的姓名；如需更稳妥，可只展示名（去掉姓）或用昵称，按机构同意书口径处理。
7. **CORS**：`BAILICAI_ALLOWED_ORIGIN` 上线建议填具体的公众号域名，而非 `*`。

## 接口一览
| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/checkin` | 上报打卡（照片仅在 is_public 且有照片时保存） |
| GET | `/api/wall` | 荣誉墙数据（仅已同意且未下架） |
| GET | `/media/<fn>` | 公开照片（校验后才返回） |
| GET | `/wall` | 荣誉墙页面 |
| GET | `/admin` | 后台（需登录） |
| POST | `/api/admin/hide/<id>` | 下架 / 恢复 |
| POST | `/api/admin/delete/<id>` | 彻底删除 |
| POST | `/api/admin/cleanup` | 清理过期数据 |

> 数据库用的是 SQLite，零配置、好交付。需要换 MySQL，把 `app.py` 里 sqlite3 的连接和建表语句替换为 MySQL 驱动即可，字段结构不变。
