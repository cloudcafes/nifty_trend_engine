def classify_signal(metrics):
    if metrics['transition']: return metrics['transition'].replace("_", " ")
    trend = metrics['trend']
    if "STRONG" in trend: return trend.replace("_", " ")
    if "EARLY" in trend: return trend.replace("_", " ")
    if metrics['fading']: return metrics['fading'].replace("_", " ")
    return "SIDEWAYS"