# calc_rmr.R — OxyViewer R 接口
# 用于 respR 批量计算耗氧率，可从 Python 通过 Rscript 调用
#
# 用法:
#   Rscript calc_rmr.R <data_folder> <params_csv> <meas_time> <channels> [chamber_csv]
#
#   参数:
#     data_folder : 数据文件夹 (如 "X:/Rtools/20260422/20260422 20")
#     params_csv  : 参数文件路径 (如 "X:/Rtools/20260422/raw/meas_params.csv")
#     meas_time   : 实验日期 (如 20260422)
#     channels    : 要计算的通道号, 逗号分隔 (如 "1,2,3" 或 "1" 或 "1-9")
#     chamber_csv : (可选) 渗透系数表 chamber.csv (列: chamber_ID, k_values)；
#                   省略或传空串则使用内置默认值
#
# 示例:
#   Rscript calc_rmr.R "X:/Rtools/20260422/20260422 20" "X:/Rtools/20260422/raw/meas_params.csv" 20260422 "1-9" "X:/Rtools/20260422/raw/chamber.csv"

args <- commandArgs(trailingOnly = TRUE)
if (length(args) < 4) {
  stop("Usage: Rscript calc_rmr.R <data_folder> <params_csv> <meas_time> <channels>")
}

library(respR)
library(lubridate)
library(readxl)

# 激活 renv 项目环境
if (Sys.getenv("RENV_PROJECT") != "") {
    renv::load(Sys.getenv("RENV_PROJECT"))
}

# 禁止生成 rplot.pdf
pdf(NULL)

# ── 参数解析 ──
data_folder <- args[1]
params_file <- args[2]
meas_time    <- as.integer(args[3])
ch_str       <- args[4]
k_file       <- if (length(args) >= 5) args[5] else ""

# 解析通道列表 (支持 "1,3,5" 或 "2-9" 或 "1")
if (grepl("-", ch_str)) {
  parts <- strsplit(ch_str, "-")[[1]]
  channel <- as.integer(parts[1]):as.integer(parts[2])
} else {
  channel <- as.integer(strsplit(ch_str, ",")[[1]])
}

# ── 渗透系数矩阵 ──
# 内置默认值：仅在未提供 chamber.csv 时使用。
# 必须与 calc_rmr.py 的 K_VALUES、templates/chamber.csv 一致，
# 改完跑 packaging/check_defaults.py 校验。
k <- matrix(data = NA, nrow = 9, ncol = 2)
colnames(k) <- c("channel", "k_value")
k[, "channel"] <- 1:9
k[, "k_value"] <- c(0.0006223, 0.000317161, 0.001055724, 0.000671915,
                    0.000537423, 0.001256691, 0.000743536, 0.000743536, 0.000743536)

# 提供了渗透系数文件 (chamber.csv: chamber_ID,k_values) 时覆盖内置默认值
if (nzchar(k_file)) {
  if (!file.exists(k_file)) {
    stop("渗透系数文件未找到: ", k_file)
  }
  kdf <- read.csv(k_file, fileEncoding = "UTF-8-BOM", stringsAsFactors = FALSE)
  cid_col <- if ("chamber_ID" %in% names(kdf)) "chamber_ID" else "channel"
  kv_col  <- if ("k_values"   %in% names(kdf)) "k_values"  else "k_value"
  n_ok <- 0
  for (i in seq_len(nrow(kdf))) {
    ch_i <- suppressWarnings(as.integer(kdf[[cid_col]][i]))
    kv_i <- suppressWarnings(as.numeric(kdf[[kv_col]][i]))
    if (!is.na(ch_i) && !is.na(kv_i) && ch_i >= 1 && ch_i <= nrow(k)) {
      k[ch_i, "k_value"] <- kv_i
      n_ok <- n_ok + 1
    }
  }
  message("  渗透系数: ", basename(k_file), " (", n_ok, " 个通道)")
} else {
  message("  渗透系数: 内置默认值")
}

# ── 读取循环参数 ──
if (!file.exists(params_file)) {
  stop("循环参数文件未找到: ", params_file)
}
params <- read.csv(params_file, fileEncoding = "UTF-8-BOM")

# ── 为每个通道匹配参数并计算 ──
for (x in channel) {

  # 从 chamber_ID 匹配参数行 (不再硬编码 rmr_type)
  mode_params <- NULL
  for (i in seq_len(nrow(params))) {
    if (params$meas_time[i] != meas_time) next
    cid <- as.character(params$chamber_ID[i])
    ids <- as.integer(unlist(strsplit(gsub("\"", "", cid), ",")))
    if (x %in% ids) {
      mode_params <- i
      break
    }
  }

  if (is.null(mode_params) || length(mode_params) == 0) {
    message("通道 ", x, " 无匹配参数，跳过")
    next
  }

  # 从匹配行读取参数
  cycles       <- params$cycles[mode_params]
  cycle_length <- params$cycle_length[mode_params]
  initial      <- params$initial[mode_params]
  cycle_start  <- params$cycle_start[mode_params]
  cycle_time   <- params$cycle_time[mode_params] - params$cycle_start[mode_params] - 5

  if (is.na(cycles) || cycles == 0) {
    message("通道 ", x, " 参数为空，跳过")
    next
  }

  # 循环时间矩阵
  cycle_mat <- matrix(data = NA, nrow = cycles, ncol = 2)
  colnames(cycle_mat) <- c("start", "end")
  cycle_mat[, "start"] <- seq(from = cycle_start + initial,
                              by = cycle_length, length.out = cycles)
  cycle_mat[, "end"]   <- cycle_mat[, "start"] + cycle_time

  # ── 读取数据 ──
  infile  <- file.path(data_folder, paste0(x, ".xlsx"))
  outfile <- file.path(paste0("rmr", x, ".csv"))

  if (!file.exists(infile)) {
    message("文件不存在: ", infile, "，跳过")
    next
  }

  df   <- read_excel(path = infile, sheet = 6)
  data <- df[, c(2, 7)]
  names(data) <- c("Date", "Oxygen")

  # 时间格式
  dtm <- parse_date_time(unlist(data$Date), "ymdHMS")
  data$Date <- format(dtm, "%y/%m/%d %H:%M:%S")
  data <- format_time(data, format = "ymdHMS")

  # 渗透系数校正
  k_value <- k[k[, "channel"] == x, "k_value"]
  max_oxy <- mean(df$Oxygen[order(df$Oxygen, decreasing = TRUE)[1:min(30, nrow(df))]],
                  na.rm = TRUE)
  data$oxy <- data$Oxygen - k_value * (max_oxy - data$Oxygen)

  # ── 计算耗氧率 ──
  dataint <- inspect(data, time = 3, oxygen = 4)
  rates <- calc_rate(dataint, from = cycle_mat[, "start"],
                     to = cycle_mat[, "end"], by = "time")
  s <- rates$summary[, 2:13]
  s$rate <- s$rate * 3600  # 转换为 mgO2·L⁻¹·h⁻¹

  # ── 导出 ──
  write.table(x = s, file = outfile, sep = ",", row.names = FALSE,
              col.names = TRUE)
  message("已保存: ", outfile)
}
