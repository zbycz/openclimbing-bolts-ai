# Training the model

```bash
cd workspace
# then run all the bash/python scripts
```

To see the whole training dataset, see [all the cropped bolts](https://zbycz.github.io/openclimbing-bolts-ai/training/).




--------


## The story

Training was a series of trials and errors. First I let Claude design a viewing/labeling app to see what data I have. I noticed that about half of the areas marked as "bolt" in OpenClimbing didn't contain any visible bolt head. That makes sense: climbers mark bolts in the topo, even when the bolt itself isn't clearly visible from the photo, sometimes the photo quality was too poor.

So I decided to label about 100 out of 3500 total, and decided to try my luck with letting some vision LLMs classifing the rest. I tested 10 different models (Moondream 2, gpt-4o/mini, nemotron-nano-12b, gemma-4-26b/31b, Qwen3-VL-8B/32B and Gemini 2.5 Flash/Flash-Lite) with no results, then I landed on Llama 4 Maverick, which gave me about 50% accuracy on my testing data. I let Claude come up with different prompts adding some ideas myself. After running all of them, i got one incredibly accurate - 95% correct on my testing set. Total cost $0.4 via OpenRouter.

I let Claude design the notebook for Google Colab, which gives you free compute power on P100 GPUs, but after few round of copy&pasting errors, I realized I need an automated way for the agent to run the notebook. Google Colab had some obstacles (eg. no official API), so I searched for an alternative finding Kaggle. This is someting similar, but with great CLI/API support.

After I gave Claude my API token, it started iterating. First it uploaded the dataset of 2.3GB original photos from Wikimedia Commons, then the labels from Maverick and then designed the training notebook.

After some time, I almost lost all hope, because it seemed (reportedly) that the in-built pytorch version on Kaggle isn't compatible with their P100. The agent tried several approaches and then stopped with "can't do it" resolution.

As a last resort i told Claude to "just do it somehow", which suprisingly worked. Claude said it escaped from the python in the notebook, instaling new python and correct version of pytorch on the bare kaggle container and finally finished with some training results.

The results were terrible - mAP50 about 4%. 

### Tiling 

First I realized that the model isn't training on the original photos, but on images resized to 1280px. Because that is the models "input layer", if i understand it correctly. Of course, that is completly unusable for miniature bolts, which are often only a few pixels wide, and 30px at best.  

So I let Claude to design some kind of tiling. It splited the originals in 1280px tiles with overlaps, and added some consolidating function for the overlapping parts.

That run finished with mAP50 about 13%, that was promising.

### Hand labeling

Then I checked out the rest of my dataset and what a surprise. The yes/no labels provided by Llama 4 Maverick seemed completely random. 

In my naive attempt for finding the correct prompt, it seems I overtrained the model for my specific testing data. But 95%? Apparently I got very "lucky".

At that point I realized I should probably label my dataset by hand. And not only yes/no, but rather marking the precise position and size of the bolt head on each candidate crop.

It was so much fun. I labeled a few pages on my laptop, but then I had to travel longer journey with buses and a train, so I switched to my iPhone 12 mini and after each page I prompted some enhancement, which made the process faster and even more fun. Also I realized that doing the hand labeling on my laptop was tedious and "work like", alas doing it my phone was just a game.

After labeling 1500 crops, I got mAP50 45%. At that point I became sure it was the right way, so in the next 24 hours I managed to get all the 3600 crops labeled and positioned, finishing with mAP50 of 51%. (For the precise positioning Claude offered to use SAM model to mark the rough size, and it was somewhat useful, but not necessary.) 

### Deploying to OpenClimbing.org

TBA

https://paste.rs/9xKHz.md
https://docs.google.com/spreadsheets/d/1nT-JMwYaCpMtI0XX8e0NP3JPiABiiUYNzllJe_LHvfQ/edit?gid=0#gid=0

### Including negative

TBA

