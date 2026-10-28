# calc_rmr.R — OxyViewer R 接口
# 用于 respR 批量计算耗氧率，可从 Python 通过 Rscript 调用
#
# 用法:
#   Rscript calc_rmr.R <data_folder> <params_csv> <meas_time> <channels> [chamber_csv] [exclude_csv]
#
#   参数:
#     data_folder  : 数据文件夹 (如 "X:/Rtools/20260422/20260422 20")
#     params_csv   : 参数文件路径 (如 "X:/Rtools/20260422/raw/meas_params.csv")
#     meas_time    : 实验日期 (如 20260422)
#     channels     : 要计算的通道号, 逗号分隔 (如 "1,2,3" 或 "1" 或 "1-9")
#     chamber_csv  : (可选) 渗透系数表 chamber.csv (列: chamber_ID, k_values)；
#                    省略或传空串则使用内置默认值
#     exclude_csv  : (可选) 排除参数文件 meas_exclude.csv (列: meas_time,
#                    meas_type, rmr_type, chamber_ID, meas_batch, exclude_cycles)；
#                    exclude_cycles 形如 "3,6,9-14"，这些循环不参与计算
#
# 示例:
#   Rscript calc_rmr.R "X:/Rtools/20260422/20260422 20" \
#     "X:/Rtools/20260422/raw/meas_params.csv" 20260422 "1-9" \
#     "X:/Rtools/20260422/raw/chamber.csv" "X:/Rtools/20260422/raw/meas_exclude.csv"

args <- commandArgs(trailingOnly = TRUE)
if (length(args) < 4) {
  stop("Usage: Rscript calc_rmr.R <data_folder> <params_csv> <meas_time> <channels> [chamber_csv] [exclude_csv]")
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

# 被排除循环要清空的「代谢结果」列（rank/row/time/oxy 等时间相关列一律保留）
# 必须与 calc_rmr.py 的 METAB_COLS 一致
METAB_COLS <- c("intercept_b0", "slope_b1", "rsq", "rate.2pt", "rate")

# ── 参数解析 ──
data_folder <- args[1]
params_file <- args[2]
meas_time    <- as.integer(args[3])
ch_str       <- args[4]
k_file       <- if (length(args) >= 5) args[5] else ""
exclude_file <- if (length(args) >= 6) args[6] else ""

# 解析排除循环表达式: "3,6,9-14" -> c(3,6,9,10,11,12,13,14)
# 支持中文逗号/顿号、空格，区间连接符可用 - ~ — – －
# 注: 与 calc_rmr.py 的 parse_cycle_spec() 行为一致
parse_cycle_spec <- function(s) {
  s <- as.character(s)
  if (length(s) == 0 || is.na(s) || !nzchar(trimws(s))) return(integer(0))
  s <- gsub("[，、；;[:space:]]", ",", s)
  parts <- unlist(strsplit(s, ",", fixed = TRUE))
  out <- integer(0)
  for (p in parts) {
    p <- trimws(p)
    if (!nzchar(p)) next
    if (grepl("[-~—–－]", p)) {
      ab <- strsplit(p, "[-~—–－]")[[1]]
      a <- suppressWarnings(as.integer(trimws(ab[1])))
      b <- suppressWarnings(as.integer(trimws(ab[2])))
      if (!is.na(a) && !is.na(b)) out <- c(out, seq(min(a, b), max(a, b)))
    } else {
      v <- suppressWarnings(as.integer(p))
      if (!is.na(v)) out <- c(out, v)
    }
  }
  sort(unique(out[out > 0]))
}

# 解析通道列表 (支持 "1,3,5"、"2-9"、混写 "1,3-5,8")
parse_channels <- function(ch_str) {
  ch_str <- gsub("，", ",", as.character(ch_str))
  out <- integer(0)
  for (p in unlist(strsplit(ch_str, ",", fixed = TRUE))) {
    p <- trimws(p)
    if (!nzchar(p)) next
    if (grepl("-", p, fixed = TRUE)) {
      ab <- strsplit(p, "-", fixed = TRUE)[[1]]
      a <- suppressWarnings(as.integer(trimws(ab[1])))
      b <- suppressWarnings(as.integer(trimws(ab[2])))
      if (!is.na(a) && !is.na(b)) out <- c(out, seq(min(a, b), max(a, b)))
    } else {
      v <- suppressWarnings(as.integer(p))
      if (!is.na(v)) out <- c(out, v)
    }
  }
  sort(unique(out[out > 0]))
}
channel <- parse_channels(ch_str)
if (length(channel) == 0) {
  stop("通道参数无法解析: ", ch_str)
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

# ── 读取排除参数 ──
# 一行一个通道，exclude_cycles 为 "3,6,9-14" 形式；这些循环不参与计算。
exclude_tbl <- NULL
if (nzchar(exclude_file)) {
  if (!file.exists(exclude_file)) {
    stop("排除参数文件未找到: ", exclude_file)
  }
  exclude_tbl <- read.csv(exclude_file, fileEncoding = "UTF-8-BOM",
                          stringsAsFactors = FALSE)
  if (!"exclude_cycles" %in% names(exclude_tbl)) {
    stop("排除参数文件缺少 exclude_cycles 列: ", exclude_file)
  }
  message("  排除参数: ", basename(exclude_file))
} else {
  message("  排除参数: 未提供（不排除任何循环）")
}

# ── 为每个通道匹配参数并计算 ──
n_ok   <- 0
n_fail <- 0
failed_ch <- integer(0)
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
  if (is.na(cycle_time) || cycle_time <= 0) {
    message("通道 ", x, " 斜率窗口时长为 ", cycle_time, "s（<=0），跳过")
    next
  }
  if (is.na(cycle_length) || cycle_length <= 0) {
    message("通道 ", x, " 周期为 ", cycle_length, "s（<=0），跳过")
    next
  }

  # 循环时间矩阵
  cycle_mat <- matrix(data = NA, nrow = cycles, ncol = 2)
  colnames(cycle_mat) <- c("start", "end")
  cycle_mat[, "start"] <- seq(from = cycle_start + initial,
                              by = cycle_length, length.out = cycles)
  cycle_mat[, "end"]   <- cycle_mat[, "start"] + cycle_time

  # ── 排除循环：从计算矩阵里剔除 ──
  excluded <- integer(0)
  if (!is.null(exclude_tbl)) {
    for (i in seq_len(nrow(exclude_tbl))) {
      if (as.character(exclude_tbl$meas_time[i]) != as.character(meas_time)) next
      cid_x <- as.character(exclude_tbl$chamber_ID[i])
      ids_x <- as.integer(unlist(strsplit(gsub("\"", "", cid_x), ",")))
      if (x %in% ids_x) {
        excluded <- parse_cycle_spec(exclude_tbl$exclude_cycles[i])
        break
      }
    }
  }
  # 注意: 排除不删行 —— rank/time/oxy 等时间相关列必须保留，
  # 只把被排除循环的「代谢结果」列置为 NA（见下方 METAB_COLS）
  if (length(excluded) > 0) {
    message("  排除循环: ", paste(sort(unique(excluded)), collapse = ","),
            "（", length(unique(excluded)), " 个，保留行、代谢数据置空）")
  }

  # ── 读取数据 ──
  infile  <- file.path(data_folder, paste0(x, ".xlsx"))
  outfile <- file.path(paste0("rmr", x, ".csv"))

  if (!file.exists(infile)) {
    message("文件不存在: ", infile, "，跳过")
    next
  }

  # ── 读取数据并计算 ──
  # 单个通道出错（数据不足、时间段超出数据范围等）只跳过该通道，
  # 不要中断整批计算；失败原因写进控制台。
  tryCatch({
    df   <- read_excel(path = infile, sheet = 6)
    data <- df[, c(2, 7)]
    names(data) <- c("Date", "Oxygen")

    # 时间格式
    dtm <- parse_date_time(unlist(data$Date), "ymdHMS")
    data$Date <- format(dtm, "%y/%m/%d %H:%M:%S")
    data <- format_time(data, format = "ymdHMS")

    # 渗透系数校正
    kv <- k[k[, "channel"] == x, "k_value"]
    if (length(kv) == 0 || is.na(kv[1])) {
      # 通道号不在内置表里（如 >9）时退回 0，避免整批计算中断
      kv <- 0
      message("  通道 ", x, " 无渗透系数，按 0 处理")
    } else {
      kv <- kv[1]
    }
    max_oxy <- mean(df$Oxygen[order(df$Oxygen, decreasing = TRUE)[1:min(30, nrow(df))]],
                    na.rm = TRUE)
    data$oxy <- data$Oxygen - kv * (max_oxy - data$Oxygen)

    # ── 计算耗氧率 ──
    dataint <- inspect(data, time = 3, oxygen = 4)
    rates <- calc_rate(dataint, from = cycle_mat[, "start"],
                       to = cycle_mat[, "end"], by = "time")
    s <- rates$summary[, 2:13]
    s$rate <- s$rate * 3600  # 转换为 mgO2·L⁻¹·h⁻¹

    # 被排除循环：行保留，只清空代谢结果列（与 calc_rmr.py 的 METAB_COLS 一致）
    if (length(excluded) > 0) {
      idx <- excluded[excluded >= 1 & excluded <= nrow(s)]
      for (col in METAB_COLS) {
        if (col %in% names(s) && length(idx) > 0) s[idx, col] <- NA
      }
    }

    # ── 导出 ──
    write.table(x = s, file = outfile, sep = ",", row.names = FALSE,
                col.names = TRUE)
    message("已保存: ", outfile)
    n_ok <<- n_ok + 1
  }, error = function(e) {
    message("通道 ", x, " 计算失败: ", conditionMessage(e), "（已跳过）")
    n_fail <<- n_fail + 1
    failed_ch <<- c(failed_ch, x)
  })
}

message("完成: 成功 ", n_ok, " 个通道",
        if (n_fail > 0)
          paste0("，失败 ", n_fail, " 个（",
                 paste(failed_ch, collapse = ","), "）") else "")
if (n_ok == 0 && n_fail > 0) {
  quit(status = 1)
}
