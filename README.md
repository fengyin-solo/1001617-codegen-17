# 机场地面保障调度平台

面向机场机坪运行的航班保障、机位资源、廊桥对接、除冰加注、行李装卸与保障结算的一体化地面调度后台。

这是一个前后端分离的管理平台：前端 Vue 3 + Vite + TypeScript，后端 FastAPI（Python）。
两边各自独立启动，前端 dev server 已关掉自动打开页面，启动后按终端打印的地址手工打开。

## 目录结构

```text
.
├── frontend/                 Vue 3 + Vite + TypeScript 前端
│   ├── src/views/            每个业务模块一个页面
│   ├── src/api/              统一请求封装
│   ├── src/stores/           会话与筛选状态
│   └── vite.config.ts        dev server 配置（open: false）
├── backend/                  FastAPI（Python） 后端
│   ├── app/routers/          每个业务模块一组接口
│   ├── app/services/         业务规则与状态流转
│   └── app/store.py          内存数据仓库与示例数据
├── .gitignore
└── docker-compose.yml
```

## 启动

### 后端

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run.sh
```

健康检查：`curl http://127.0.0.1:8000/api/health`

### 前端

```bash
cd frontend
npm install
npm run dev
```

前端默认监听 `http://127.0.0.1:5173/`，dev server 不会自动打开浏览器，
需要自己访问。`/api` 由 vite 代理到后端 `http://127.0.0.1:8000`。

## 业务模块

| 模块 | 目录 | 业务对象 | 主要字段 |
| --- | --- | --- | --- |
| 航班计划 | `flight` | 航班计划 | 航班号、执行日期、机型 |
| 机位资源 | `stand` | 机位 | 机位编号、机位类别、所属区域 |
| 机坪巡查 | `apron` | 巡查单 | 巡查单号、巡查区域、巡查人员 |
| 廊桥对接 | `bridge` | 对接任务 | 对接单号、关联航班、廊桥编号 |
| 除冰作业 | `deicing` | 除冰单 | 除冰单号、关联航班、除冰方式 |
| 航油加注 | `fueling` | 加注单 | 加注单号、关联航班、加注车号 |
| 行李装卸 | `baggage` | 装卸单 | 装卸单号、关联航班、行李件数 |
| 货邮装载 | `cargo` | 装载单 | 装载单号、关联航班、货邮重量 |
| 航空配餐 | `catering` | 配餐单 | 配餐单号、关联航班、餐食份数 |
| 摆渡接送 | `shuttle` | 摆渡任务 / 航班波次派车 | 任务编号、关联航班、车辆编号、乘客人数、出发/到达时刻 |
| 航空器牵引 | `towing` | 牵引任务 | 牵引编号、关联航班、牵引车号 |
| 载重平衡 | `loadsheet` | 配载单 | 配载单号、关联航班、计算重量 |
| 通行证件 | `permit` | 通行证件 | 证件编号、持证人员、所属单位 |
| 保障车辆 | `gse` | 保障车辆 | 车辆编号、车辆类别、适用作业 |
| 安全监察 | `safety` | 监察记录 | 监察编号、监察区域、监察事项 |
| 保障协议 | `agreement` | 保障协议 | 协议编号、服务单位、保障项目 |
| 保障结算 | `settlement` | 结算单 | 结算单号、关联协议、结算周期 |
| 资质培训 | `training` | 培训记录 | 培训编号、培训主题、培训对象 |

## 约定

- 每个模块的前端页面在 `frontend/src/views/<模块>/index.vue`，后端接口在
  `backend/app/routers/<模块>.py`，业务规则在 `backend/app/services/<模块>.py`。
- 列表接口统一返回 `{ items, total, page, size }`，动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。

## 摆渡接送：按航班波次派车

摆渡页「按波次派车」先选航班波次（早班/午间/晚班示例见 `app/seed.py` 中的
`shuttle_wave`、`shuttle_wave_flight`、`shuttle_vehicle` 三张表），一次生成多条候选摆渡任务：

1. 按乘客人数分配车辆：在载客量够用的车里选容量最小、编号最靠前、且时段空闲的车。
2. 逐条校验出发时刻必须早于到达时刻，以及车辆在该时段是否已被占用。
3. 车辆不够（含单车容量不足、车辆维修停用）或时段冲突的候选任务单独列出，不允许勾选；
   可派任务支持勾选后只提交其中一部分。
4. 同一车辆同一时段在同一批次里重复派车会整组挡住，并互相点名冲突的候选任务编号；
   与已派车任务冲突时提示占用车辆的正式任务编号。
5. 已派车任务可通过「导出摆渡清单」下载 CSV（带 BOM，Excel 可直接打开），导出沿用列表页
   的筛选条件与列，末尾附任务条数与乘客人数合计，与页面清点一致。
6. 写操作成功后会把全量数据快照到 `backend/data/snapshot.json`，浏览器刷新或服务重启后
   数据自动读回；删除该文件即可恢复内置示例数据。
