from app.ingestion.html_text import (
    choose_better_text,
    html_to_article_text,
    scrub_extracted_text,
    text_quality_score,
)


def test_html_to_article_text_prefers_article_and_drops_scripts() -> None:
    html = """
    <html><head><script>window.dataLayer=window.dataLayer||[];gtag('config','G');</script></head>
    <body>
      <nav>首页</nav>
      <article>
        <h1>示例公司业绩预告</h1>
        <p>公司预计2026年半年度净利润同比增长20%至30%。</p>
        <p>上述预测基于当前经营状况，不构成投资承诺。</p>
      </article>
      <footer>下载APP</footer>
      <script>console.log('x')</script>
    </body></html>
    """
    text = html_to_article_text(html)
    assert "净利润同比增长" in text
    assert "不构成投资承诺" in text
    assert "dataLayer" not in text
    assert "gtag" not in text
    assert "下载APP" not in text


def test_html_to_article_text_handles_rss_summary_fragment() -> None:
    html = (
        '<p><img src="https://example.com/a.png" /></p>'
        "<p>面对创纪录的二季度交付数据，管理层重申扩张计划。</p>"
        "<p>美东时间22日美股盘后，特斯拉公布二季度财报。</p>"
    )
    text = html_to_article_text(html)
    assert "二季度交付数据" in text
    assert "特斯拉公布二季度财报" in text


def test_scrub_extracted_text_removes_legacy_js_noise() -> None:
    dirty = (
        "window.dataLayer = window.dataLayer || [] function gtag() { dataLayer.push(arguments) }\n"
        "曼德海峡34%运量蒸发：当沙特的B计划延布港也成为靶心 - 华尔街见闻\n"
        "首页\n资讯\n股票\n{json}\n硬AI\nVIP会员\n关于我们\n"
        "红海危机持续升级后，绕行好望角推高航运成本。"
    )
    cleaned = scrub_extracted_text(dirty)
    assert "dataLayer" not in cleaned
    assert "运量蒸发" in cleaned
    assert "航运成本" in cleaned
    assert "首页" not in cleaned
    assert "硬AI" not in cleaned
    assert "VIP会员" not in cleaned


def test_choose_better_text_prefers_readable_summary_over_page_noise() -> None:
    page = "window.dataLayer=[];gtag('js');" + ("x" * 200)
    summary = "公司预计净利润同比增长20%至30%。董事会已审议相关预案。"
    chosen = choose_better_text(page, summary)
    assert "净利润同比增长" in chosen
    assert text_quality_score(summary) > text_quality_score(page)


def test_html_to_article_text_handles_article_header_and_void_tags() -> None:
    """回归：article>header + img 空元素不得打穿 skip_depth。"""
    html = """
    <html><body>
      <article class="x">
        <header><h1>特斯拉电话会</h1>
          <div><img src="https://example.com/a.png" /></div>
        </header>
        <p>面对创纪录的二季度交付数据，马斯克重申扩张计划。</p>
        <p>美东时间22日美股盘后，特斯拉公布二季度财报。</p>
      </article>
    </body></html>
    """
    text = html_to_article_text(html)
    assert "二季度交付数据" in text
    assert "二季度财报" in text
    assert "dataLayer" not in text
