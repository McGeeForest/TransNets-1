import os
import pickle
import time

import torch
from torch.nn import functional as F
from torch.utils.data import DataLoader

from config import Config
from model import SourceNet, TargetNet
from utils import date, calculate_mse, TransNetsDataset


def train(train_dataloader, valid_dataloader, model_S, model_T, config, model_path):
    print(f'{date()}## Start the training!')
    train_mse = calculate_mse(model_S, train_dataloader, config.device)
    valid_mse = calculate_mse(model_S, valid_dataloader, config.device)
    print(f'{date()}#### Initial train mse {train_mse:.6f}, validation mse {valid_mse:.6f}')
    start_time = time.perf_counter()

    opt_S = torch.optim.Adam(model_S.parameters(), config.learning_rate, weight_decay=config.l2_regularization)
    opt_trans = torch.optim.Adam(model_S.trans_param(), config.learning_rate, weight_decay=config.l2_regularization)
    opt_T = torch.optim.Adam(model_T.parameters(), config.learning_rate, weight_decay=config.l2_regularization)
    lr_sch_S = torch.optim.lr_scheduler.ExponentialLR(opt_S, config.learning_rate_decay)
    lr_sch_trans = torch.optim.lr_scheduler.ExponentialLR(opt_trans, config.learning_rate_decay)
    lr_sch_T = torch.optim.lr_scheduler.ExponentialLR(opt_T, config.learning_rate_decay)

    best_loss, best_epoch, batch_step = 100, 0, 0
    model_T.train()
    for epoch in range(config.train_epochs):
        model_S.train()  # turn on the train
        total_loss, total_samples = 0, 0
        for batch in train_dataloader:
            user_reviews, item_reviews, reviews, ratings, user_ids, item_ids = [x.to(config.device) for x in batch]
            # step 1: Train Target Network on the actual review.
            latent_T, pred_T = model_T(reviews)
            loss_T = F.l1_loss(pred_T, ratings)
            opt_T.zero_grad()
            loss_T.backward()
            # step 2: Learn to Transform.
            latent_S, pred_S = model_S(user_reviews, item_reviews, user_ids, item_ids)
            loss_trans = F.mse_loss(latent_S, latent_T.detach())
            opt_trans.zero_grad()
            loss_trans.backward()
            # step 3: Train a predictor on the transformed input.
            loss_S = F.l1_loss(pred_S, ratings, reduction='sum')
            opt_S.zero_grad()
            loss_S.backward()

            opt_T.step()
            opt_trans.step()
            opt_S.step()

            batch_step += 1
            total_loss += loss_S.item()  # summing over all loss of source network
            total_samples += len(pred_S)
            if batch_step % 500 == 0:  # valid per 500 steps.
                model_S.eval()
                valid_mse = calculate_mse(model_S, valid_dataloader, config.device)
                if best_loss > valid_mse:
                    best_loss = valid_mse
                    torch.save(model_S, model_path)
                print(f"{date()}###### Step {batch_step:3d}; validation mse {valid_mse:.6f}")
                model_S.train()
        lr_sch_S.step()
        lr_sch_trans.step()
        lr_sch_T.step()
        model_S.eval()
        valid_mse = calculate_mse(model_S, valid_dataloader, config.device)
        if best_loss > valid_mse:
            best_loss = valid_mse
            torch.save(model_S, model_path)
        train_loss = total_loss / total_samples
        print(f"{date()}#### Epoch {epoch:3d}; train mse {train_loss:.6f}; validation mse {valid_mse:.6f}")

    end_time = time.perf_counter()
    print(f'{date()}## End of training! Time used {end_time - start_time:.0f} seconds.')


def test(dataloader, best_model):
    print(f'{date()}## Start the testing!')
    start_time = time.perf_counter()
    test_loss = calculate_mse(best_model, dataloader, config.device)
    end_time = time.perf_counter()
    print(f"{date()}## Test end, test mse is {test_loss:.6f}, time used {end_time - start_time:.0f} seconds.")


if __name__ == '__main__':
    config = Config()
    print(config)
    print(f'{date()}## Load word2vec and data...')

    word_emb = pickle.load(open('embedding/word_emb.pkl', 'rb'), encoding='iso-8859-1')
    word_dict = pickle.load(open('embedding/dict.pkl', 'rb'), encoding='iso-8859-1')

    train_dataset = TransNetsDataset(config.train_file, word_dict, config)
    valid_dataset = TransNetsDataset(config.valid_file, word_dict, config)
    test_dataset = TransNetsDataset(config.test_file, word_dict, config)
    train_dlr = DataLoader(train_dataset, batch_size=config.batch_size, shuffle=True)
    valid_dlr = DataLoader(valid_dataset, batch_size=config.batch_size, shuffle=True)
    test_dlr = DataLoader(test_dataset, batch_size=config.batch_size, shuffle=True)

    source_model = SourceNet(config, word_emb, extend_model=config.extension).to(config.device)
    target_model = TargetNet(config, word_emb).to(config.device)
    del train_dataset, valid_dataset, test_dataset, word_emb, word_dict

    os.makedirs(os.path.dirname(config.model_file), exist_ok=True)  # make dir if it isn't exist.
    train(train_dlr, valid_dlr, source_model, target_model, config, config.model_file)
    test(test_dlr, torch.load(config.model_file))
