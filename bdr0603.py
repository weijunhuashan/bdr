#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
批量从PACS检索DICOM文件
支持按Patient ID或Patient Name搜索
支持CT/MR模态选择
支持日期范围过滤
支持传输完成校验
支持命令行直接输入ID/Name或从文件读取
"""

import os
import time
import logging
import argparse
from datetime import datetime
from tqdm import tqdm
from collections import defaultdict

from pynetdicom import AE, evt
from pynetdicom.sop_class import (
    MRImageStorage,
    CTImageStorage,
    PatientRootQueryRetrieveInformationModelMove,
    PatientRootQueryRetrieveInformationModelFind
)
from pydicom.dataset import Dataset
from pydicom.uid import ExplicitVRLittleEndian

# =========================================================
# 固定PACS配置（不可修改）
# =========================================================
PACS_HOST = '128.0.71.35'
PACS_PORT = 4100
PACS_AET = 'PACS'
LOCAL_PORT = 4096
LOCAL_AET = 'OSIRIX'

# PACS_HOST = '128.0.72.12'
# PACS_PORT = 106
# PACS_AET = 'WINQR'
# LOCAL_PORT = 11112
# LOCAL_AET = 'HOROS'

# 模态映射
MODALITY_MAP = {
    'MR': MRImageStorage,
    'MRI': MRImageStorage,
    'CT': CTImageStorage
}

# =========================================================
# 解析命令行参数
# =========================================================
def parse_args():
    parser = argparse.ArgumentParser(
        description='批量从PACS检索DICOM文件',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
搜索方式:
  1. 按Patient ID搜索: 使用 -pid 或 --pid
  2. 按Patient Name搜索: 使用 -pname 或 --pname

使用示例:
  # 命令行直接输入单个Patient ID
  python batch_dcm_retrieve.py -m MRI -pid 12345678

  # 命令行直接输入多个Patient ID（空格分隔）
  python batch_dcm_retrieve.py -m MRI -pid 12345678 87654321 11223344

  # 命令行直接输入单个Patient Name
  python batch_dcm_retrieve.py -m CT -pname "SMITH^JOHN"

  # 命令行直接输入多个Patient Name
  python batch_dcm_retrieve.py -m CT -pname "SMITH^JOHN" "DOE^JANE" "BROWN^BOB"

  # 从文件读取Patient ID列表
  python batch_dcm_retrieve.py -m MRI -pid-file patient_ids.txt

  # 从文件读取Patient Name列表
  python batch_dcm_retrieve.py -m CT -pname-file patient_names.txt

  # 指定日期范围
  python batch_dcm_retrieve.py -m MRI -pid 12345678 --date-start 20240101 --date-end 20241231

  # 自定义输出目录
  python batch_dcm_retrieve.py -m CT -pname "SMITH^JOHN" -o ./ct_data

  # 启用严格校验模式
  python batch_dcm_retrieve.py -m MR -pid 12345678 --strict-verify

  # 混合使用（支持多个ID/Name）
  python batch_dcm_retrieve.py -m MR -pid 12345678 87654321 -o ./output --date-start 20240101
        '''
    )
    
    # 必需参数 - 模态选择
    parser.add_argument(
        '-m', '--modality',
        type=str,
        required=True,
        choices=['MR', 'MRI', 'CT'],
        help='数据类型: MR/MRI 或 CT'
    )
    
    # 搜索参数组 - 支持命令行直接输入或文件输入
    search_group = parser.add_argument_group('搜索参数 (二选一，必须提供一种)')
    
    # Patient ID相关参数
    pid_group = search_group.add_mutually_exclusive_group()
    pid_group.add_argument(
        '-pid', '--patient-id',
        type=str,
        nargs='+',  # 支持多个值
        help='Patient ID，支持多个（空格分隔），例如: -pid 12345678 87654321'
    )
    pid_group.add_argument(
        '-pid-file', '--patient-id-file',
        type=str,
        help='按Patient ID搜索，指定Patient ID文件路径（每行一个ID）'
    )
    
    # Patient Name相关参数
    pname_group = search_group.add_mutually_exclusive_group()
    pname_group.add_argument(
        '-pname', '--patient-name',
        type=str,
        nargs='+',  # 支持多个值
        help='Patient Name，支持多个（空格分隔），例如: -pname "SMITH^JOHN" "DOE^JANE"'
    )
    pname_group.add_argument(
        '-pname-file', '--patient-name-file',
        type=str,
        help='按Patient Name搜索，指定Patient Name文件路径（每行一个姓名）'
    )
    
    # 确保至少提供一种搜索方式
    required_group = parser.add_argument_group('必需参数')
    
    # 可选参数 - 输入输出
    parser.add_argument(
        '-o', '--output-dir',
        type=str,
        default='dicom_output',
        help='输出目录 (默认: dicom_output)'
    )
    
    # 可选参数 - 日期范围过滤
    parser.add_argument(
        '--date-start',
        type=str,
        help='开始日期，格式: YYYYMMDD (例如: 20240101)'
    )
    parser.add_argument(
        '--date-end',
        type=str,
        help='结束日期，格式: YYYYMMDD (例如: 20241231)'
    )
    
    # 可选参数 - 重试配置
    parser.add_argument(
        '--max-retry',
        type=int,
        default=10,
        help='失败重试次数 (默认: 10)'
    )
    parser.add_argument(
        '--retry-interval',
        type=int,
        default=2,
        help='重试间隔秒数 (默认: 2)'
    )
    
    # 可选参数 - 其他选项
    parser.add_argument(
        '--no-progress',
        action='store_true',
        help='不显示进度条'
    )
    parser.add_argument(
        '--log-file',
        type=str,
        default='dicom_retrieve.log',
        help='日志文件路径 (默认: dicom_retrieve.log)'
    )
    parser.add_argument(
        '--strict-verify',
        action='store_true',
        help='严格校验模式：验证接收文件数量与PACS报告数量是否一致'
    )
    parser.add_argument(
        '--debug',
        action='store_true',
        help='启用调试模式，显示详细的DICOM通信日志'
    )
    
    return parser.parse_args()


def validate_search_args(args):
    """验证搜索参数是否至少提供了一种"""
    has_search = (args.patient_id is not None or 
                  args.patient_id_file is not None or
                  args.patient_name is not None or 
                  args.patient_name_file is not None)
    
    if not has_search:
        raise ValueError("必须提供搜索参数: -pid, -pid-file, -pname, 或 -pname-file")
    
    # 检查是否同时提供了ID和Name搜索
    id_search = (args.patient_id is not None or args.patient_id_file is not None)
    name_search = (args.patient_name is not None or args.patient_name_file is not None)
    
    if id_search and name_search:
        raise ValueError("不能同时提供Patient ID和Patient Name搜索，请选择一种")


# =========================================================
# 验证日期格式
# =========================================================
def validate_date(date_str, param_name):
    """验证日期格式是否为YYYYMMDD"""
    if date_str is None:
        return None
    try:
        datetime.strptime(date_str, '%Y%m%d')
        return date_str
    except ValueError:
        raise ValueError(f"{param_name} 格式错误，应为 YYYYMMDD，实际: {date_str}")


# =========================================================
# 读取搜索关键字列表（支持命令行直接输入或文件）
# =========================================================
def get_search_keys(args):
    """获取搜索关键字列表，支持命令行直接输入或文件读取"""
    
    if args.patient_id is not None:
        keys = list(args.patient_id)
        search_type = "Patient ID"
        logging.info(f"从命令行读取 {len(keys)} 个Patient ID")
        return keys, search_type
    
    if args.patient_id_file is not None:
        keys = read_search_keys_from_file(args.patient_id_file, "Patient ID")
        search_type = "Patient ID"
        return keys, search_type
    
    if args.patient_name is not None:
        keys = list(args.patient_name)
        search_type = "Patient Name"
        logging.info(f"从命令行读取 {len(keys)} 个Patient Name")
        return keys, search_type
    
    if args.patient_name_file is not None:
        keys = read_search_keys_from_file(args.patient_name_file, "Patient Name")
        search_type = "Patient Name"
        return keys, search_type
    
    return None, None


def read_search_keys_from_file(file_path, search_type):
    """从文件读取搜索关键字列表"""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            keys = [l.strip() for l in f if l.strip()]
        
        if not keys:
            logging.error(f"文件中没有找到任何{search_type}")
            return None
        
        logging.info(f"从文件读取 {len(keys)} 个{search_type}")
        return keys
    except FileNotFoundError:
        logging.error(f"找不到文件: {file_path}")
        return None
    except Exception as e:
        logging.error(f"读取文件失败: {e}")
        return None


# =========================================================
# 带校验的存储处理器
# =========================================================
class VerifiedStoreHandler:
    """带文件计数功能的存储处理器"""
    
    def __init__(self, output_dir, date_start=None, date_end=None):
        self.output_dir = output_dir
        self.date_start = date_start
        self.date_end = date_end
        self.received_files = defaultdict(list)
        self.filtered_count = defaultdict(int)
        self.current_search_key = None
        
    def set_current_search_key(self, search_key):
        """设置当前检索的关键字"""
        self.current_search_key = search_key
    
    def get_handler(self):
        """返回C-STORE事件处理函数"""
        def handle_store(event):
            ds = event.dataset
            ds.file_meta = event.file_meta
            
            patient_id = getattr(ds, "PatientID", "UNKNOWN_ID")
            patient_name = getattr(ds, "PatientName", "UNKNOWN_NAME")
            study_date = getattr(ds, "StudyDate", "")
            series_desc = getattr(ds, "SeriesDescription", "")
            sop_instance_uid = getattr(ds, "SOPInstanceUID", "UNKNOWN_UID")
            
            # 日期范围过滤
            filtered = False
            if self.date_start or self.date_end:
                if study_date:
                    if self.date_start and study_date < self.date_start:
                        logging.debug(f"日期过滤跳过 {patient_id}: StudyDate {study_date} < {self.date_start}")
                        filtered = True
                    elif self.date_end and study_date > self.date_end:
                        logging.debug(f"日期过滤跳过 {patient_id}: StudyDate {study_date} > {self.date_end}")
                        filtered = True
                else:
                    logging.debug(f"日期过滤跳过 {patient_id}: 无StudyDate")
                    filtered = True
            
            if filtered:
                if self.current_search_key:
                    self.filtered_count[self.current_search_key] += 1
                return 0x0000
            
            # 构建保存路径
            safe_name = "".join(c for c in str(patient_name) if c.isalnum() or c in ' _-')
            if safe_name:
                dir_name = f"{patient_id}_{safe_name}"
            else:
                dir_name = patient_id
            
            save_dir = os.path.join(self.output_dir, dir_name)
            if study_date:
                save_dir = os.path.join(save_dir, study_date)
            if series_desc:
                safe_series_desc = "".join(c for c in series_desc if c.isalnum() or c in ' _-')
                if safe_series_desc:
                    save_dir = os.path.join(save_dir, safe_series_desc[:50])
            
            os.makedirs(save_dir, exist_ok=True)
            
            filename = f"{sop_instance_uid}.dcm"
            filepath = os.path.join(save_dir, filename)
            
            # 避免重复保存
            if os.path.exists(filepath):
                logging.debug(f"文件已存在，跳过: {filepath}")
                if self.current_search_key:
                    self.received_files[self.current_search_key].append({
                        'filepath': filepath,
                        'sop_instance_uid': sop_instance_uid,
                        'patient_id': patient_id,
                        'study_date': study_date,
                        'duplicate': True
                    })
                return 0x0000
            
            # ==================== 修复后的保存文件逻辑 ====================
            try:
                # 显式保障元数据完整性
                if not hasattr(ds, 'file_meta') or not getattr(ds.file_meta, 'TransferSyntaxUID', None):
                    ds.file_meta = event.file_meta
                    if not getattr(ds.file_meta, 'TransferSyntaxUID', None):
                        ds.file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
                
                # write_like_original=False 确保严格按照 DICOM Part 10 写入 128 字节文件头及 "DICM" 魔法字符
                ds.save_as(filepath, write_like_original=False)
                    
            except Exception as e:
                logging.warning(f"保存文件时出错，尝试备用方法: {e}")
                try:
                    ds.save_as(filepath)
                except Exception as e2:
                    logging.error(f"无法保存文件 {filepath}: {e2}")
                    return 0xC000
            # ============================================================
            
            # 记录接收的文件
            if self.current_search_key:
                self.received_files[self.current_search_key].append({
                    'filepath': filepath,
                    'sop_instance_uid': sop_instance_uid,
                    'patient_id': patient_id,
                    'study_date': study_date,
                    'duplicate': False
                })
            
            logging.debug(f"已保存: {filepath}")
            return 0x0000
        
        return handle_store
    
    def get_file_count(self, search_key):
        """获取指定检索关键字实际保存的文件数量"""
        files = self.received_files.get(search_key, [])
        return len([f for f in files if not f.get('duplicate', False)])
    
    def get_filtered_count(self, search_key):
        """获取被日期过滤跳过的文件数量"""
        return self.filtered_count.get(search_key, 0)
    
    def get_file_list(self, search_key):
        """获取指定检索关键字接收的文件列表"""
        return self.received_files.get(search_key, [])


# =========================================================
# 解析C-MOVE响应状态（累积计数）
# =========================================================
class CMoveResponse:
    """C-MOVE响应解析器 - 累积计数"""
    
    def __init__(self):
        self.status_code = None
        self.completed = 0
        self.remaining = 0
        self.failed = 0
        self.warning = 0
        self.is_success = False
        self.has_data = False
        
    def parse(self, status):
        """解析C-MOVE响应状态 - 累积更新计数"""
        if status is None:
            return
        
        self.status_code = status.Status
        
        current_completed = getattr(status, 'NumberOfCompletedSuboperations', None)
        current_remaining = getattr(status, 'NumberOfRemainingSuboperations', None)
        current_failed = getattr(status, 'NumberOfFailedSuboperations', None)
        current_warning = getattr(status, 'NumberOfWarningSuboperations', None)
        
        if current_completed is not None:
            self.completed = current_completed
        if current_remaining is not None:
            self.remaining = current_remaining
        if current_failed is not None:
            self.failed = current_failed
        if current_warning is not None:
            self.warning = current_warning
        
        if self.completed > 0:
            self.has_data = True
        
        if self.status_code == 0x0000:
            self.is_success = True
        elif self.status_code in (0xFF00, 0xFF01):
            self.is_success = False
        elif self.status_code == 0xB000:
            self.is_success = (self.completed > 0)
        else:
            self.is_success = False
    
    def is_complete(self):
        return self.remaining == 0 and self.failed == 0
    
    def __str__(self):
        return (f"状态: {hex(self.status_code) if self.status_code else 'None'}, "
                f"完成: {self.completed}, 剩余: {self.remaining}, "
                f"失败: {self.failed}, 警告: {self.warning}")


# =========================================================
# 执行C-MOVE查询（按Patient ID）
# =========================================================
def retrieve_by_patient_id(ae_scu, patient_id, modality, max_retry, retry_interval, 
                           no_progress, verify_handler=None, strict_verify=False):
    """按Patient ID检索"""
    
    for attempt in range(1, max_retry + 1):
        if attempt > 1:
            logging.info(f"重试 {attempt}/{max_retry} for Patient ID: {patient_id}")
        
        assoc = ae_scu.associate(
            PACS_HOST,
            PACS_PORT,
            ae_title=PACS_AET
        )
        
        if not assoc.is_established:
            logging.error(f"无法建立Association for {patient_id}")
            time.sleep(retry_interval)
            continue
        
        ds = Dataset()
        ds.QueryRetrieveLevel = "PATIENT"
        ds.PatientID = patient_id
        if modality:
            ds.Modality = modality
        
        cumulative_response = CMoveResponse()
        
        if verify_handler:
            previous_count = verify_handler.get_file_count(patient_id)
            previous_filtered = verify_handler.get_filtered_count(patient_id)
        
        try:
            responses = assoc.send_c_move(
                ds,
                move_aet=LOCAL_AET,
                query_model=PatientRootQueryRetrieveInformationModelMove
            )
            
            iterator = responses
            if not no_progress:
                iterator = tqdm(
                    responses,
                    desc=f"{patient_id[:20]}",
                    leave=False
                )
            
            for status, _ in iterator:
                if status is None:
                    continue
                
                response = CMoveResponse()
                response.parse(status)
                cumulative_response.parse(status)
                
                if response.status_code in (0xFF00, 0xFF01):
                    if not no_progress:
                        iterator.set_postfix(
                            remaining=cumulative_response.remaining,
                            completed=cumulative_response.completed,
                            failed=cumulative_response.failed
                        )
                else:
                    logging.info(f"C-MOVE最终响应: {cumulative_response}")
            
            assoc.release()
            
            final_completed = cumulative_response.completed
            final_failed = cumulative_response.failed
            final_remaining = cumulative_response.remaining
            
            logging.info(f"传输统计汇总: 完成={final_completed}, 失败={final_failed}, 剩余={final_remaining}")
            
            if final_completed > 0:
                if verify_handler:
                    received_count = verify_handler.get_file_count(patient_id) - previous_count
                    filtered_count = verify_handler.get_filtered_count(patient_id) - previous_filtered
                    
                    logging.info(f"文件统计: PACS发送={final_completed}, 实际保存={received_count}, 日期过滤={filtered_count}")
                    
                    if received_count + filtered_count != final_completed:
                        missing = final_completed - (received_count + filtered_count)
                        logging.warning(f"数据不完整: 丢失 {missing} 个文件")
                        
                        if strict_verify and missing > final_completed * 0.3:
                            logging.error(f"严格校验模式: 丢失超过30%文件，视为失败")
                            time.sleep(retry_interval)
                            continue
                    
                    if received_count > 0:
                        logging.info(f"✓ 成功保存 {received_count} 个DICOM文件")
                    elif filtered_count > 0:
                        logging.info(f"✓ 所有 {filtered_count} 个文件均被日期过滤跳过")
                    else:
                        logging.warning(f"未保存任何文件，但PACS报告发送了 {final_completed} 个文件")
                        time.sleep(3)
                        received_count = verify_handler.get_file_count(patient_id) - previous_count
                        if received_count > 0:
                            logging.info(f"延迟接收: 实际保存 {received_count} 个文件")
                
                if verify_handler:
                    received_count = verify_handler.get_file_count(patient_id) - previous_count
                    filtered_count = verify_handler.get_filtered_count(patient_id) - previous_filtered
                    if received_count > 0 or filtered_count > 0:
                        return True
                    elif final_completed > 0:
                        time.sleep(5)
                        received_count = verify_handler.get_file_count(patient_id) - previous_count
                        if received_count > 0:
                            return True
                        elif not strict_verify:
                            return True
                else:
                    return True
                    
            elif final_completed == 0 and final_failed == 0 and final_remaining == 0:
                logging.info(f"该患者无符合条件的数据")
                return True
            else:
                logging.error(f"C-MOVE传输失败: 完成={final_completed}, 失败={final_failed}, 剩余={final_remaining}")
                
        except Exception as e:
            logging.exception(f"异常: {e}")
            assoc.release()
        
        time.sleep(retry_interval)
    
    return False


# =========================================================
# 执行C-MOVE查询（按Patient Name）
# =========================================================
def retrieve_by_patient_name(ae_scu, patient_name, modality, max_retry, retry_interval, 
                             no_progress, verify_handler=None, strict_verify=False):
    """按Patient Name检索"""
    
    for attempt in range(1, max_retry + 1):
        if attempt > 1:
            logging.info(f"重试 {attempt}/{max_retry} for Patient Name: {patient_name}")
        
        assoc = ae_scu.associate(
            PACS_HOST,
            PACS_PORT,
            ae_title=PACS_AET
        )
        
        if not assoc.is_established:
            logging.error(f"无法建立Association for {patient_name}")
            time.sleep(retry_interval)
            continue
        
        ds = Dataset()
        ds.QueryRetrieveLevel = "PATIENT"
        ds.PatientName = patient_name
        if modality:
            ds.Modality = modality
        
        cumulative_response = CMoveResponse()
        
        if verify_handler:
            previous_count = verify_handler.get_file_count(patient_name)
            previous_filtered = verify_handler.get_filtered_count(patient_name)
        
        try:
            responses = assoc.send_c_move(
                ds,
                move_aet=LOCAL_AET,
                query_model=PatientRootQueryRetrieveInformationModelMove
            )
            
            iterator = responses
            if not no_progress:
                iterator = tqdm(
                    responses,
                    desc=f"{patient_name[:20]}",
                    leave=False
                )
            
            for status, _ in iterator:
                if status is None:
                    continue
                
                response = CMoveResponse()
                response.parse(status)
                cumulative_response.parse(status)
                
                if response.status_code in (0xFF00, 0xFF01):
                    if not no_progress:
                        iterator.set_postfix(
                            remaining=cumulative_response.remaining,
                            completed=cumulative_response.completed,
                            failed=cumulative_response.failed
                        )
                else:
                    logging.info(f"C-MOVE最终响应: {cumulative_response}")
            
            assoc.release()
            
            final_completed = cumulative_response.completed
            final_failed = cumulative_response.failed
            final_remaining = cumulative_response.remaining
            
            logging.info(f"传输统计汇总: 完成={final_completed}, 失败={final_failed}, 剩余={final_remaining}")
            
            if final_completed > 0:
                if verify_handler:
                    received_count = verify_handler.get_file_count(patient_name) - previous_count
                    filtered_count = verify_handler.get_filtered_count(patient_name) - previous_filtered
                    
                    logging.info(f"文件统计: PACS发送={final_completed}, 实际保存={received_count}, 日期过滤={filtered_count}")
                    
                    if received_count + filtered_count != final_completed:
                        missing = final_completed - (received_count + filtered_count)
                        logging.warning(f"数据不完整: 丢失 {missing} 个文件")
                        
                        if strict_verify and missing > final_completed * 0.3:
                            logging.error(f"严格校验模式: 丢失超过30%文件，视为失败")
                            time.sleep(retry_interval)
                            continue
                    
                    if received_count > 0:
                        logging.info(f"✓ 成功保存 {received_count} 个DICOM文件")
                    elif filtered_count > 0:
                        logging.info(f"✓ 所有 {filtered_count} 个文件均被日期过滤跳过")
                    else:
                        logging.warning(f"未保存任何文件，但PACS报告发送了 {final_completed} 个文件")
                        time.sleep(3)
                        received_count = verify_handler.get_file_count(patient_name) - previous_count
                        if received_count > 0:
                            logging.info(f"延迟接收: 实际保存 {received_count} 个文件")
                
                if verify_handler:
                    received_count = verify_handler.get_file_count(patient_name) - previous_count
                    filtered_count = verify_handler.get_filtered_count(patient_name) - previous_filtered
                    if received_count > 0 or filtered_count > 0:
                        return True
                    elif final_completed > 0:
                        time.sleep(5)
                        received_count = verify_handler.get_file_count(patient_name) - previous_count
                        if received_count > 0:
                            return True
                        elif not strict_verify:
                            return True
                else:
                    return True
                    
            elif final_completed == 0 and final_failed == 0 and final_remaining == 0:
                logging.info(f"该患者无符合条件的数据")
                return True
            else:
                logging.error(f"C-MOVE传输失败: 完成={final_completed}, 失败={final_failed}, 剩余={final_remaining}")
                
        except Exception as e:
            logging.exception(f"异常: {e}")
            assoc.release()
        
        time.sleep(retry_interval)
    
    return False


# =========================================================
# 打印最终校验报告
# =========================================================
def print_verification_report(verify_handler, search_keys, search_type, strict_verify, date_start, date_end):
    """打印最终的校验报告"""
    logging.info("\n" + "=" * 70)
    logging.info("传输校验报告")
    logging.info("=" * 70)
    
    if date_start or date_end:
        date_range = []
        if date_start:
            date_range.append(f" ≥ {date_start}")
        if date_end:
            date_range.append(f" ≤ {date_end}")
        logging.info(f"日期过滤: {' 且 '.join(date_range)}")
    
    total_files = 0
    total_filtered = 0
    success_keys = []
    empty_keys = []
    
    for key in search_keys:
        file_count = verify_handler.get_file_count(key)
        filtered_count = verify_handler.get_filtered_count(key)
        total_files += file_count
        total_filtered += filtered_count
        
        if file_count > 0:
            success_keys.append((key, file_count, filtered_count))
        elif filtered_count > 0:
            success_keys.append((key, 0, filtered_count))
        else:
            empty_keys.append(key)
    
    logging.info(f"检索关键字总数: {len(search_keys)}")
    logging.info(f"有数据的关键字: {len(success_keys)}")
    logging.info(f"无数据的关键字: {len(empty_keys)}")
    logging.info(f"总保存文件数: {total_files}")
    if total_filtered > 0:
        logging.info(f"总过滤文件数: {total_filtered} (因日期范围)")
    
    if success_keys:
        logging.info("\n成功检索详情:")
        for key, count, filtered in success_keys[:10]:
            if count > 0:
                logging.info(f"  {key}: {count} 个文件")
            elif filtered > 0:
                logging.info(f"  {key}: 0 个文件 ({filtered} 个被日期过滤)")
        if len(success_keys) > 10:
            logging.info(f"  ... 共{len(success_keys)}个关键字")
    
    if empty_keys:
        logging.info(f"\n无数据的关键字 (前10个):")
        for key in empty_keys[:10]:
            logging.info(f"  {key}")
        if len(empty_keys) > 10:
            logging.info(f"  ... 共{len(empty_keys)}个关键字")
    
    logging.info("=" * 70)


# =========================================================
# 主函数
# =========================================================
def main():
    args = parse_args()
    
    try:
        validate_search_args(args)
    except ValueError as e:
        logging.error(f"参数错误: {e}")
        print(f"\n错误: {e}\n")
        print("请使用 -h 查看帮助信息")
        return
    
    if args.debug:
        logging.basicConfig(level=logging.DEBUG, format="%(asctime)s | %(levelname)s | %(message)s")
        logging.getLogger('pynetdicom').setLevel(logging.DEBUG)
    else:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
        logging.getLogger('pydicom').setLevel(logging.ERROR)
        logging.getLogger('pynetdicom').setLevel(logging.WARNING)
    
    date_start = validate_date(args.date_start, '--date-start')
    date_end = validate_date(args.date_end, '--date-end')
    
    modality = args.modality.upper()
    if modality == 'MRI':
        modality = 'MR'
    
    sop_class = MODALITY_MAP.get(modality)
    if sop_class is None:
        logging.error(f"不支持的模态: {modality}")
        return
    
    search_keys, search_type = get_search_keys(args)
    
    if search_keys is None or len(search_keys) == 0:
        logging.error("没有找到任何搜索关键字")
        return
    
    os.makedirs(args.output_dir, exist_ok=True)
    
    log_format = "%(asctime)s | %(levelname)s | %(message)s"
    file_handler = logging.FileHandler(args.log_file)
    file_handler.setFormatter(logging.Formatter(log_format))
    logging.getLogger().addHandler(file_handler)
    
    logging.info("=" * 70)
    logging.info("批量DICOM检索配置")
    logging.info("=" * 70)
    logging.info(f"  PACS地址: {PACS_HOST}:{PACS_PORT}")
    logging.info(f"  搜索方式: {search_type}")
    logging.info(f"  搜索数量: {len(search_keys)}")
    logging.info(f"  模态: {modality}")
    logging.info(f"  本地AE: {LOCAL_AET}")
    logging.info(f"  本地端口: {LOCAL_PORT}")
    logging.info(f"  输出目录: {args.output_dir}")
    if date_start:
        logging.info(f"  开始日期: {date_start}")
    if date_end:
        logging.info(f"  结束日期: {date_end}")
    logging.info(f"  最大重试: {args.max_retry}")
    logging.info(f"  严格校验: {'启用' if args.strict_verify else '禁用'}")
    logging.info(f"  调试模式: {'启用' if args.debug else '禁用'}")
    logging.info(f"  日志文件: {args.log_file}")
    logging.info("=" * 70)
    
    preview_count = min(5, len(search_keys))
    logging.info(f"搜索关键字预览 (前{preview_count}个):")
    for i in range(preview_count):
        logging.info(f"  {i+1}. {search_keys[i]}")
    if len(search_keys) > preview_count:
        logging.info(f"  ... 共{len(search_keys)}个")
    logging.info("=" * 70)
    
    verify_handler = VerifiedStoreHandler(args.output_dir, date_start, date_end)
    
    store_handler = verify_handler.get_handler()
    handlers = [(evt.EVT_C_STORE, store_handler)]
    
    ae_scp = AE(ae_title=LOCAL_AET)
    ae_scp.add_supported_context(sop_class)
    
    try:
        ae_scp.start_server(
            ("0.0.0.0", LOCAL_PORT),
            block=False,
            evt_handlers=handlers
        )
        logging.info(f"存储SCP已启动，监听端口 {LOCAL_PORT}")
    except OSError as e:
        logging.error(f"端口 {LOCAL_PORT} 已被占用: {e}")
        logging.error("请检查是否已有程序占用该端口，或修改LOCAL_PORT")
        return
    
    ae_scu = AE(ae_title=LOCAL_AET)
    ae_scu.add_requested_context(PatientRootQueryRetrieveInformationModelMove)
    ae_scu.add_requested_context(PatientRootQueryRetrieveInformationModelFind)
    
    if search_type == "Patient ID":
        def search_with_id(key):
            verify_handler.set_current_search_key(key)
            return retrieve_by_patient_id(
                ae_scu, key, modality, args.max_retry, args.retry_interval, 
                args.no_progress, verify_handler, args.strict_verify
            )
        search_func = search_with_id
    else:
        def search_with_name(key):
            verify_handler.set_current_search_key(key)
            return retrieve_by_patient_name(
                ae_scu, key, modality, args.max_retry, args.retry_interval,
                args.no_progress, verify_handler, args.strict_verify
            )
        search_func = search_with_name
    
    success_count = 0
    fail_count = 0
    
    for idx, search_key in enumerate(search_keys, 1):
        logging.info(f"\n[{idx}/{len(search_keys)}] 处理 {search_type}: {search_key}")
        
        success = search_func(search_key)
        
        if success:
            success_count += 1
            file_count = verify_handler.get_file_count(search_key)
            filtered_count = verify_handler.get_filtered_count(search_key)
            if file_count > 0:
                logging.info(f"✓ 成功: {search_type}={search_key} (接收 {file_count} 个文件)")
            elif filtered_count > 0:
                logging.info(f"✓ 成功: {search_type}={search_key} (所有 {filtered_count} 个文件被日期过滤)")
            else:
                logging.info(f"✓ 成功: {search_type}={search_key} (无数据)")
        else:
            logging.error(f"✗ 失败: {search_type}={search_key} (已达最大重试次数)")
            fail_count += 1
    
    logging.info("正在关闭存储SCP...")
    ae_scp.shutdown()
    time.sleep(1)
    
    print_verification_report(verify_handler, search_keys, search_type, args.strict_verify, date_start, date_end)
    
    logging.info("\n" + "=" * 70)
    logging.info("检索完成统计")
    logging.info("=" * 70)
    logging.info(f"  搜索方式: {search_type}")
    logging.info(f"  成功: {success_count}")
    logging.info(f"  失败: {fail_count}")
    logging.info(f"  总计: {len(search_keys)}")
    if len(search_keys) > 0:
        logging.info(f"  成功率: {success_count/len(search_keys)*100:.1f}%")
    logging.info(f"  输出目录: {args.output_dir}")
    logging.info(f"  日志文件: {args.log_file}")
    logging.info("=" * 70)


if __name__ == "__main__":
    main()