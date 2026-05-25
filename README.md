# bdr
Batch Dicom File Retrive
# 批量DICOM检索工具 - 安装与操作手册

pip install pynetdicom pydicom tqdm

## 配置说明

### PACS服务器配置

```python
PACS_HOST = ' '   # PACS服务器地址
PACS_PORT =              # PACS端口
PACS_AET = ' '            # PACS AE Title
LOCAL_PORT =              # 本地监听端口
LOCAL_AET = ' '         # 本地AE Title
```

## 使用方法

### 基本语法

```bash
python dicom_retrieve.py -m <模态> [搜索参数] [选项]
```

### 搜索参数说明

| 参数 | 说明 | 示例 |
|------|------|------|
| `-m` | 模态（必需） | `MR`, `MRI`, `CT` |
| `-pid` | Patient ID（支持多个） | `-pid 12345678` |
| `-pid-file` | Patient ID文件 | `-pid-file ids.txt` |
| `-pname` | Patient Name（支持多个） | `-pname "SMITH^JOHN"` |
| `-pname-file` | Patient Name文件 | `-pname-file names.txt` |
| `-o` | 输出目录 | `-o ./output` |
| `--date-start` | 开始日期 | `--date-start 20240101` |
| `--date-end` | 结束日期 | `--date-end 20241231` |
| `--max-retry` | 最大重试次数 | `--max-retry 10` |
| `--strict-verify` | 严格校验模式 | `--strict-verify` |
| `--debug` | 调试模式 | `--debug` |
| `--no-progress` | 不显示进度条 | `--no-progress` |

### 使用示例

#### 1. 检索单个患者（命令行直接输入）

```bash
# 检索单个Patient ID的MR图像
python dicom_retrieve.py -m MR -pid 13456730

# 检索单个Patient Name的CT图像
python dicom_retrieve.py -m CT -pname "SMITH^JOHN"

# 指定输出目录
python dicom_retrieve.py -m MRI -pid 13456730 -o ./my_patient_data
```

#### 2. 检索多个患者（命令行直接输入）

```bash
# 多个Patient ID
python dicom_retrieve.py -m MR -pid 13456730 87654321 11223344

# 多个Patient Name
python dicom_retrieve.py -m CT -pname "SMITH^JOHN" "DOE^JANE" "BROWN^BOB"
```

#### 3. 从文件批量检索

**创建Patient ID文件** (`patient_ids.txt`)：
```
13456730
87654321
11223344
99887766
```

**创建Patient Name文件** (`patient_names.txt`)：
```
SMITH^JOHN
DOE^JANE
BROWN^BOB
```

**执行检索**：
```bash
# 从文件读取Patient ID
python dicom_retrieve.py -m MR -pid-file patient_ids.txt

# 从文件读取Patient Name
python dicom_retrieve.py -m CT -pname-file patient_names.txt
```

#### 4. 带日期范围过滤

```bash
# 只检索2024年的数据
python dicom_retrieve.py -m MR -pid 13456730 --date-start 20240101 --date-end 20241231

# 检索指定日期之后的数据
python dicom_retrieve.py -m CT -pid 13456730 --date-start 20240601
```

#### 5. 高级选项

```bash
# 严格校验模式（确保数据完整性）
python dicom_retrieve.py -m MR -pid 13456730 --strict-verify

# 调试模式（查看详细日志）
python dicom_retrieve.py -m CT -pid 13456730 --debug

# 自定义重试次数
python dicom_retrieve.py -m MR -pid 13456730 --max-retry 5 --retry-interval 3

# 完整示例
python dicom_retrieve.py -m MR -pid 13456730 87654321 -o ./output \
    --date-start 20240101 --date-end 20241231 --strict-verify --max-retry 10
```

### 输出文件结构

检索后的文件按以下结构组织：

```
输出目录/
├── PatientID_PatientName/
│   ├── 20240101/          # 检查日期
│   │   ├── Series1/       # 序列描述
│   │   │   ├── 1.2.3.4.5.dcm
│   │   │   └── 1.2.3.4.6.dcm
│   │   └── Series2/
│   │       └── 1.2.3.4.7.dcm
│   └── 20240102/
│       └── ...
└── PatientID2_PatientName2/
    └── ...
```

### 日志文件

程序运行后生成 `dicom_retrieve.log`，包含详细的操作记录和错误信息。

