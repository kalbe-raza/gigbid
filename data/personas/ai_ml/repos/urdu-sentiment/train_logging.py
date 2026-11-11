import wandb

wandb.init(project="review-sentiment", config=cfg)
for epoch in range(cfg.epochs):
    loss = train_epoch()
    wandb.log({"loss": loss, "macro_f1": evaluate()})
